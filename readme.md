# LCDS Media & Impact Tracker

A Streamlit application designed to track media mentions and potential talks for the Leverhulme Centre for Demographic Science.

## Features
- **Smart Media Tracking:** Uses Google News RSS to find mentions of academics.
- **Noise Filtering:** Validates news results against "Context Keywords" (e.g., must mention "Oxford" or "Demographic") to remove irrelevant people with the same name.
- **Project Tracking:** Tracks mentions of the Centre or specific datasets even when no specific author is named.
- **Manual Entry:** Form to manually log Keynotes/Talks.

## Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
