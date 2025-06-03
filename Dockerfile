# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /app

# Create a directory for persistent data inside the container
# This is where your JSON file will live (but be mapped externally)
RUN mkdir -p /app/data

# Install build essentials including gcc
# This step will allow packages that need C compilation to build
RUN apt-get update && \
    apt-get install -y gcc build-essential libc6-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Command to run your bot when the container launches
# Replace 'main.py' with the name of your main Python file that starts the bot
CMD ["python", "bot.py"]