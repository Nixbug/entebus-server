"""
Validation Patterns (Regex)

These constants define the regular expressions used across the system
for validating common fields such as usernames, passwords, and vehicle
registration numbers. Centralizing them ensures consistency and
reusability across the codebase.
"""

# Username must start with a letter and can include letters, numbers,
# and special characters (- . @ _)
USERNAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9-.@_]*$"

# Password can include letters, numbers, and a wide range of special characters
PASSWORD_PATTERN = r"^[a-zA-Z0-9-+,.@_$%&*#!^=/?]*$"

# Matches most international vehicle plates
# - Letters and digits
# - Optional separators (space, dash, dot)
# - 2 to 10 characters per group, up to 3 groups
VEHICLE_NUMBER_PATTERN = r"^([A-Z0-9]{1,4}[-. ]?){1,3}[A-Z0-9]{1,4}$"

# Role name must start with a letter and can include letters, numbers, special characters and spaces
NAME_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]*[A-Za-z0-9])?$"
