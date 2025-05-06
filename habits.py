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
        "unit": "minutes",    # using numeric for pages
        "min": 10,
        "reply": lambda pages: f"**reading** for **{pages} pages**",
    },
    "walking": {
        "unit": "minutes",
        "min": 30,
        "reply": lambda mins: f"**walking** for **{mins} min**",
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
        "weekly_target": 4,
        "reply": lambda _: "**diet**",
    },
    "bedtime": {
        "unit": "bool",
        "weekly_target": 4,
        "reply": lambda _: "**11pm bedtime**",
    },
    "streaming": {
        "unit": "minutes",
        "min": 0,
        "max": 60,
        "reply": lambda mins: f"**streaming** for **{mins} min**",
    },
}