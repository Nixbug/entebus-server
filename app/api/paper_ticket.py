# """
# Paper Ticket API Router for EnteBus.

# Provides endpoints for managing paper tickets, including creation.
# Uses Pydantic schemas for input validation and structured output.
# Endpoints for retrieval are planned for future implementation.
# """

# from dataclasses import Field
# from datetime import datetime
# from app.src.urls import URL_PAPER_TICKET
# from fastapi import APIRouter
# from pydantic import BaseModel




# from app.src.digital_ticket.v1 import TicketSchema, TicketType
# from app.src.enums import AppID


# route_operator = APIRouter()

# ## Output Schema
# class PaperTicketSchema(TicketSchema):
#     """Schema for paper ticket response."""

#     id : int
#     service_id : int
#     duty_id : int
#     company_id : int
#     ticket : TicketSchema
#     amount : float
#     created_on : datetime


# ## Input Forms
# class CreateForm(Basemodel):
#     """form data for creating a new paper ticket."""

#     service_id : int = Field()
#     ticket : TicketSchema = Field()
#     amount : float = Field()



# # ---------------------------------------------------------------------------
# ## API endpoints [Operator]
# # ---------------------------------------------------------------------------
# # create post endpoint for paper ticket

