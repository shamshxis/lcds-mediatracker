# config.py

# CORE SETTINGS
CENTER_NAME = "Leverhulme Centre for Demographic Science"
INSTITUTION = "University of Oxford"

# CONTEXT KEYWORDS
# If a news article mentions a name but NOT one of these words, it is likely noise.
# We include "LCDS", "Oxford", and general topic keywords.
# config.py
CONTEXT_KEYWORDS = [
    "Oxford",
    "Leverhulme",
    "LCDS",
    "Demographic",
    "Population",
    "Sociology",
    "Nuffield",
    "Social Science",
    "Study",
    "Research",
    "Professor",
    "Dr",
]

# PROJECT KEYWORDS (The "Unnamed" Tracker)
# Search terms for the centre itself or specific major projects, 
# in case the academic isn't named.
PROJECT_KEYWORDS = [
    "Leverhulme Centre for Demographic Science",
    "LCDS Oxford",
    "COVID-19 impact map", # Example project
    "Digital Gender Gap", # Example project
]

# DEFAULT ACADEMIC LIST (You can override this in the app UI)
DEFAULT_NAMES = [
    "Melinda Mills",
    "Jennifer Dowd",
    "Ridhi Kashyap",
    "Per Block"
]
