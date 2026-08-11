from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, List, Dict
from base91 import encode, decode
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from pydantic import BaseModel, Field
from app.src.exceptions import InvalidTicketVersion, InvalidDigitalTicket

TwoDecimalPlaces = Annotated[Decimal, Field(max_digits=10, decimal_places=2, ge=0)]


# Schema definitions for ticket data
class TicketTypeSchema(BaseModel):
    id: int = Field(ge=1, le=255)
    count: int = Field(ge=1, le=255)
    price: TwoDecimalPlaces


class TicketSchema(BaseModel):
    id: int
    service_id: int
    created_on: datetime
    ticket_types: List[TicketTypeSchema]
    amount: TwoDecimalPlaces
    pickup_point: int
    dropping_point: int
    distance: int
    extras: Dict[str, Any] = Field(default_factory=dict)


class DigitalTicket:
    """
    Represents a digitally signed ticket object which can be serialized to a string
    and deserialized from a string.

    Attributes:
        VERSION (int): Ticket format version.
        SIGNATURE (bytes): The digital signature of the ticket body.
        BODY (bytes): The content of the ticket, including fixed and variable parts.
    """

    def __init__(self, signature: bytes, body: bytes):
        self.version = 1
        self.signature = signature
        self.body = body

    def __str__(self) -> str:
        """
        Serializes the ticket to a base91-encoded string.
        Format: <VERSION><ENCODED_SIGNATURE+BODY>

        Returns:
            str: Serialized digital ticket.
        """
        return str(self.version) + encode(self.signature + self.body)

    @staticmethod
    def load(digital_ticket: str) -> "DigitalTicket":
        """
        Deserializes a digital ticket from its base91-encoded string representation.

        Args:
            digital_ticket (str): The encoded digital ticket string.

        Returns:
            DigitalTicket: The deserialized ticket object.
        """
        try:
            VERSION = int(digital_ticket[0])
        except (ValueError, TypeError, IndexError):
            raise InvalidDigitalTicket()

        if VERSION != 1:
            raise InvalidTicketVersion()

        body_and_signature = decode(digital_ticket[1:])
        minimum_payload_size = (
            TicketCreator.SIGNATURE_SIZE + TicketCreator.FIXED_PART_SIZE
        )
        if len(body_and_signature) < minimum_payload_size:
            raise InvalidDigitalTicket()

        ticket_signature = body_and_signature[: TicketCreator.SIGNATURE_SIZE]
        ticket_body = body_and_signature[TicketCreator.SIGNATURE_SIZE :]
        return DigitalTicket(ticket_signature, ticket_body)

    def expand(self, ticket_attributes: dict) -> dict:
        """
        Expands the ticket by decoding the body and populating the provided ticket_attributes dict.

        Args:
            ticket_attributes (dict): A dictionary containing ticket type definitions.

        Returns:
            dict: Updated dictionary with extracted ticket details.
        """
        fixed_part = self.body[: TicketCreator.FIXED_PART_SIZE]
        variable_part = self.body[TicketCreator.FIXED_PART_SIZE :]

        # Validate variable_part encoding: it should be a sequence of 2-byte pairs
        # (ticket_type_id, count). If it's not even-length, it's malformed.
        if len(variable_part) % 2 != 0:
            raise InvalidDigitalTicket()

        ticket_id_bytes = fixed_part[:4]
        pickup_point_bytes = fixed_part[4:8]
        dropping_point_bytes = fixed_part[8:]

        ticket_id = int.from_bytes(ticket_id_bytes, byteorder="big", signed=False)
        pickup_point = int.from_bytes(pickup_point_bytes, byteorder="big", signed=False)
        dropping_point = int.from_bytes(
            dropping_point_bytes, byteorder="big", signed=False
        )

        ticket_data = ticket_attributes
        ticket_data["id"] = ticket_id
        ticket_data["pickup_point"] = pickup_point
        ticket_data["dropping_point"] = dropping_point

        # Parse ticket types: pairs of (ticket_type_id, count)
        for i in range(0, len(variable_part), 2):
            ticket_type_id = variable_part[i]
            ticket_count = variable_part[i + 1]

            for ticket_type in ticket_data["ticket_types"]:
                if ticket_type["id"] == ticket_type_id:
                    ticket_type["count"] = ticket_count
        return ticket_data


class TicketCreator:
    """
    A utility class for generating and verifying digital tickets using ECDSA signatures.

    Attributes:
        SIGNATURE_SIZE (int): Fixed size for encoded signature.
        FIXED_PART_SIZE (int): Size of fixed portion of the ticket body.
        R_COMPONENT_SIZE (int): Size of R component of ECDSA signature.
        S_COMPONENT_SIZE (int): Size of S component of ECDSA signature.
    """

    SIGNATURE_SIZE = 42  # Bytes
    FIXED_PART_SIZE = 24  # Bytes
    R_COMPONENT_SIZE = int(SIGNATURE_SIZE / 2)
    S_COMPONENT_SIZE = int(SIGNATURE_SIZE / 2)

    def __init__(
        self, pem_private_key: bytes | None = None, pem_public_key: bytes | None = None
    ):
        """
        Initializes the TicketCreator with optional PEM keys.
        If not provided, a new SECT163K1 key pair will be generated.

        Args:
            pem_private_key (bytes | None, optional): PEM-encoded private key.
            pem_public_key (bytes | None, optional): PEM-encoded public key.
        """
        if pem_private_key and pem_public_key:
            self._private_key = serialization.load_pem_private_key(
                pem_private_key, password=None
            )
            self._public_key = serialization.load_pem_public_key(pem_public_key)
        else:
            self._private_key = ec.generate_private_key(ec.SECT163K1())
            self._public_key = self._private_key.public_key()

    def create_ticket(
        self,
        id: int,
        pickup_landmark_id: int,
        dropping_landmark_id: int,
        ticket_types: list[dict],
    ) -> DigitalTicket:
        """
        Creates a digitally signed ticket.

        Args:
            id (int): Unique ticket ID.
            pickup_landmark_id (int): Boarding landmark ID.
            dropping_landmark_id (int): Destination landmark ID.
            ticket_types (list of dict): List of ticket types with `id` and `count`.

        Returns:
            DigitalTicket: The created digital ticket.
        """
        ticket_id_bytes = id.to_bytes(8, byteorder="big", signed=False)
        pickup_point_bytes = pickup_landmark_id.to_bytes(
            8, byteorder="big", signed=False
        )
        dropping_point_bytes = dropping_landmark_id.to_bytes(
            8, byteorder="big", signed=False
        )
        fixed_part = ticket_id_bytes + pickup_point_bytes + dropping_point_bytes

        # Create the variable part of the ticket
        variable_part = bytearray()
        # Loop through the ticket types and add them to the variable part
        # (1 byte ticket_type_id + 1 byte ticket_count)
        for ticket_type in ticket_types:
            if ticket_type["count"] > 0:
                ticket_type_id: int = ticket_type["id"]
                ticket_type_id_byte = ticket_type_id.to_bytes(
                    1, byteorder="big", signed=False
                )
                ticket_count: int = ticket_type["count"]
                ticket_count_byte = ticket_count.to_bytes(
                    1, byteorder="big", signed=False
                )
                variable_part += ticket_type_id_byte + ticket_count_byte
        ticket_body = fixed_part + variable_part

        # Create the digital signature and construct the digital ticket
        encoded_ticket_signature = self.private_key.sign(
            ticket_body, ec.ECDSA(hashes.SHA256())
        )
        # Decode DER signature to get r and s
        r, s = decode_dss_signature(encoded_ticket_signature)
        ticket_signature = r.to_bytes(
            self.R_COMPONENT_SIZE, byteorder="big"
        ) + s.to_bytes(self.S_COMPONENT_SIZE, byteorder="big")
        return DigitalTicket(ticket_signature, ticket_body)

    def verify(self, digital_ticket: DigitalTicket) -> bool:
        """
        Verifies the digital signature of a ticket using the public key.

        Args:
            digital_ticket (DigitalTicket): The ticket to verify.

        Returns:
            bool: True if the signature is valid, False otherwise.
        """
        # Use r and s to generate a DER-encoded signature
        r = int.from_bytes(
            digital_ticket.signature[: self.R_COMPONENT_SIZE], byteorder="big"
        )
        s = int.from_bytes(
            digital_ticket.signature[self.R_COMPONENT_SIZE :], byteorder="big"
        )
        encoded_ticket_signature = encode_dss_signature(r, s)

        # Verify the signature with the public key
        try:
            self.public_key.verify(
                encoded_ticket_signature, digital_ticket.body, ec.ECDSA(hashes.SHA256())
            )
            return True
        except Exception:
            return False

    def get_pem_private_key_bytes(self) -> bytes:
        """
        Serializes the private key to PEM format.

        Returns:
            bytes: PEM-encoded private key.
        """
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def get_pem_public_key_bytes(self) -> bytes:
        """
        Serializes the public key to PEM format.

        Returns:
            bytes: PEM-encoded public key.
        """
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @property
    def pem_private_key_string(self) -> str:
        """
        Serializes the private key to PEM format.

        Returns:
            str: PEM-encoded private key.
        """
        return self.get_pem_private_key_bytes().decode("utf-8")

    @property
    def pem_public_key_string(self) -> str:
        """
        Serializes the public key to PEM format.

        Returns:
            str: PEM-encoded public key.
        """
        return self.get_pem_public_key_bytes().decode("utf-8")

    @property
    def private_key(self) -> ec.EllipticCurvePrivateKey:
        """
        Returns the private key object.

        Returns:
            ec.EllipticCurvePrivateKey: The private key.
        """
        return self._private_key

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        """
        Returns the public key object.

        Returns:
            ec.EllipticCurvePublicKey: The public key.
        """
        return self._public_key
