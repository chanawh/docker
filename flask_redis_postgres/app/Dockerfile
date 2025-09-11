# app/Dockerfile
# Use a slim version of Python as the base image for a smaller footprint
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    # ... other build dependencies if needed
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to take advantage of Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port your Flask app runs on
EXPOSE 5000

# Set the entrypoint to run your Flask application
ENTRYPOINT ["flask"]
CMD ["run", "--host=0.0.0.0"]
