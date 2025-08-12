# habits.py

HABITS = {
    "meditation": {
        "unit": "minutes",
        "min": 30,
        "reply": lambda mins: f"**meditation** for **{mins} min**",
    },
    "exercise": {
        "unit": "bool",
        "weekly_target": 4,
        "reply": lambda _: "**exercise**",
    },
    "reading": {
        "unit": "minutes",    # pages
        "min": 10,
        "reply": lambda pages: f"**reading** for **{pages} pages**",
    },
    "walking": {
        "unit": "bool",
        "weekly_target": 4,
        "reply": lambda _: "**walking**",
    },
    "porn": {
        "unit": "bool",
        "weekly_target": 7,
        "reply": lambda _: "**no porn**",
    },
    "pmo": {
        "unit": "bool",
        "weekly_target": 7,
        "reply": lambda _: "**no PMO**",
    },
    "diet": {
        "unit": "bool",
        "weekly_target": 7,
        "reply": lambda _: "**diet**",
    },
    "bedtime": {
        "unit": "bool",
        "weekly_target": 5,
        "reply": lambda _: "**11pm bedtime**",
    },
    "streaming": {
        "unit": "bool",
        "weekly_target": 5,
        "reply": lambda _: "**no streaming**",
    },
    "journaling": {
        "unit": "bool",
        "weekly_target": 7,
        "reply": lambda _: "**journaling**",
    },
    "digitaldetox": {
        "unit": "minutes",
        "min": 15,
        "reply": lambda mins: f"**digital detox** for **{mins} min**",
    },
}