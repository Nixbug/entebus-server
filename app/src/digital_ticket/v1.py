from base91 import encode, decode
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)


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
        self.VERSION = 1
        self.SIGNATURE = signature
        self.BODY = body

    def __str__(self) -> str:
        """
        Serializes the ticket to a base91-encoded string.
        Format: <VERSION><ENCODED_SIGNATURE+BODY>

        Returns:
            str: Serialized digital ticket.
        """
        return str(self.VERSION) + encode(self.SIGNATURE + self.BODY)

    @staticmethod
    def load(digital_ticket: str) -> "DigitalTicket":
        """
        Deserializes a digital ticket from its base91-encoded string representation.

        Args:
            digital_ticket (str): The encoded digital ticket string.

        Returns:
            DigitalTicket: The deserialized ticket object.
        """

        VERSION = int(digital_ticket[0])
        assert VERSION == 1, "Unsupported ticket version"

        BODY_AND_SIGNATURE = decode(digital_ticket[1:])
        TICKET_SIGNATURE = BODY_AND_SIGNATURE[: TicketCreator.SIGNATURE_SIZE]
        TICKET_BODY = BODY_AND_SIGNATURE[TicketCreator.SIGNATURE_SIZE :]
        return DigitalTicket(TICKET_SIGNATURE, TICKET_BODY)

    def expand(self, ticket_attributes: dict) -> dict:
        """
        Expands the ticket by decoding the body and populating the provided ticket_attributes dict.

        Args:
            ticket_attributes (dict): A dictionary containing ticket type definitions.

        Returns:
            dict: Updated dictionary with extracted ticket details.
        """
        FIXED_PART = self.BODY[: TicketCreator.FIXED_PART_SIZE]
        VARIABLE_PART = self.BODY[TicketCreator.FIXED_PART_SIZE :]

        INT_32_ticketID = FIXED_PART[:4]
        INT_32_pickupPoint = FIXED_PART[4:8]
        INT_32_droppingPoint = FIXED_PART[8:]

        ticketID = int.from_bytes(INT_32_ticketID, byteorder="big", signed=False)
        pickupPoint = int.from_bytes(INT_32_pickupPoint, byteorder="big", signed=False)
        droppingPoint = int.from_bytes(
            INT_32_droppingPoint, byteorder="big", signed=False
        )

        ticketData = ticket_attributes
        ticketData["id"] = ticketID
        ticketData["pickup_point"] = pickupPoint
        ticketData["dropping_point"] = droppingPoint

        # Parse ticket types: pairs of (ticketTypeID, count)
        for i in range(0, len(VARIABLE_PART), 2):
            ticketTypeID = VARIABLE_PART[i]
            ticketCount = VARIABLE_PART[i + 1]

            for ticketType in ticketData["ticket_types"]:
                if ticketType["id"] == ticketTypeID:
                    ticketType["count"] = ticketCount
        return ticketData


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
    FIXED_PART_SIZE = 12  # Bytes
    R_COMPONENT_SIZE = int(SIGNATURE_SIZE / 2)
    S_COMPONENT_SIZE = int(SIGNATURE_SIZE / 2)

    def __init__(self, pem_private_key: bytes = None, pem_public_key: bytes = None):
        """
        Initializes the TicketCreator with optional PEM keys.
        If not provided, a new SECT163K1 key pair will be generated.

        Args:
            pem_private_key (bytes, optional): PEM-encoded private key.
            pem_public_key (bytes, optional): PEM-encoded public key.
        """

        if pem_private_key and pem_public_key:
            self.privateKey = serialization.load_pem_private_key(
                pem_private_key, password=None
            )
            self.publicKey = serialization.load_pem_public_key(pem_public_key)
        else:
            self.privateKey = ec.generate_private_key(ec.SECT163K1())
            self.publicKey = self.privateKey.public_key()

    def createTicket(
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
        INT_32_ticketID = id.to_bytes(4, byteorder="big", signed=False)
        INT_32_pickupPoint = pickup_landmark_id.to_bytes(
            4, byteorder="big", signed=False
        )
        INT_32_droppingPoint = dropping_landmark_id.to_bytes(
            4, byteorder="big", signed=False
        )
        FIXED_PART = INT_32_ticketID + INT_32_pickupPoint + INT_32_droppingPoint

        # Create the variable part of the ticket
        VARIABLE_PART = bytearray()
        # Loop through the ticket types and add them to the variable part
        # (1 byte ticketTypeID + 1 byte ticketTypeCount)
        for ticketType in ticket_types:
            if ticketType["count"] > 0:
                ticketTypeID: int = ticketType["id"]
                INT_8_ticketTypeID = ticketTypeID.to_bytes(
                    1, byteorder="big", signed=False
                )
                ticketCount: int = ticketType["count"]
                INT_8_ticketCount = ticketCount.to_bytes(
                    1, byteorder="big", signed=False
                )
                VARIABLE_PART += INT_8_ticketTypeID + INT_8_ticketCount
        TICKET_BODY = FIXED_PART + VARIABLE_PART

        # Create the digital signature and construct the digital ticket
        ENCODED_TICKET_SIGNATURE = self.privateKey.sign(
            TICKET_BODY, ec.ECDSA(hashes.SHA256())
        )
        # Decode DER signature to get r and s
        r, s = decode_dss_signature(ENCODED_TICKET_SIGNATURE)
        TICKET_SIGNATURE = r.to_bytes(
            self.R_COMPONENT_SIZE, byteorder="big"
        ) + s.to_bytes(self.S_COMPONENT_SIZE, byteorder="big")
        return DigitalTicket(TICKET_SIGNATURE, TICKET_BODY)

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
            digital_ticket.SIGNATURE[: self.R_COMPONENT_SIZE], byteorder="big"
        )
        s = int.from_bytes(
            digital_ticket.SIGNATURE[self.S_COMPONENT_SIZE :], byteorder="big"
        )
        ENCODED_TICKET_SIGNATURE = encode_dss_signature(r, s)

        # Verify the signature with the public key
        try:
            self.publicKey.verify(
                ENCODED_TICKET_SIGNATURE, digital_ticket.BODY, ec.ECDSA(hashes.SHA256())
            )
            return True
        except Exception:
            return False

    def getPEMprivateKeyBytes(self) -> bytes:
        """
        Serializes the private key to PEM format.

        Returns:
            bytes: PEM-encoded private key.
        """
        return self.privateKey.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def getPEMpublicKeyBytes(self) -> bytes:
        """
        Serializes the public key to PEM format.

        Returns:
            bytes: PEM-encoded public key.
        """
        return self.publicKey.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def getPEMprivateKeyString(self) -> str:
        """
        Serializes the private key to PEM format.

        Returns:
            str: PEM-encoded private key.
        """
        return self.getPEMprivateKeyBytes().decode("utf-8")

    def getPEMpublicKeyString(self) -> str:
        """
        Serializes the public key to PEM format.

        Returns:
            str: PEM-encoded public key.
        """
        return self.getPEMpublicKeyBytes().decode("utf-8")

    def getPrivateKey(self) -> ec.EllipticCurvePrivateKey:
        """
        Returns the private key object.

        Returns:
            ec.EllipticCurvePrivateKey: The private key.
        """
        return self.privateKey

    def getPublicKey(self) -> ec.EllipticCurvePublicKey:
        """
        Returns the public key object.

        Returns:
            ec.EllipticCurvePublicKey: The public key.
        """
        return self.publicKey
