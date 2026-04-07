from fastapi import FastAPI, HTTPException
import os
import google.generativeai as genai
from dotenv import load_dotenv
from typing import List
import google.generativeai as genai
import json
import time
import seaborn as sns
import pandas as pd

genai.configure(api_key="AIzaSyAogPEvYUJLJokjsV0oz1zl3_L81BKTcAY")

# List available models and supported generation methods:
print("Available models and supported generation methods:")
available_models = []
for m in genai.list_models():
    methods = getattr(m, 'supported_generation_methods', [])
    print(f"{m.name}: {methods}")
    if 'generateContent' in methods:
        available_models.append(m.name)

print(f"\nModels supporting generateContent: {available_models}\n")

# Prefer current Gemini 2.x/2.5 model names and skip legacy 1.5 names
preferred_order = [
    'models/gemini-2.0-flash',
    'models/gemini-2.5-pro',
    'models/gemini-2.5-flash',
    'models/gemini-2.5-flash-lite',
    'models/gemini-pro-latest',
    'models/gemini-flash-latest',
    'models/gemini-2.0-flash-lite',
]

ordered_models = [name for name in preferred_order if name in available_models]
for name in available_models:
    if name not in ordered_models:
        ordered_models.append(name)

# Try preferred models first, then all supported models
model = None
for model_name in ordered_models:
    try:
        print(f"Attempting Gemini model: {model_name}")
        model = genai.GenerativeModel(model_name)
        print(f"✓ Successfully loaded model: {model_name}")
        break
    except Exception as e:
        print(f"✗ Gemini model '{model_name}' failed: {e}")

if model is None:
    raise Exception(
        "No available Gemini models found. "
        "Use one of the current supported Gemini 2.x/2.5 models listed above."
)

def load_jsonl_data(file_path):
    data = []
    with open(file_path, 'r') as f:
        for line in f:
            data.append(json.loads(line))
    return data

training_data = load_jsonl_data("jsonl file name")
base_model = 'models/gemini-1.5-flash-001-tuning'

operation = genai.create_tuned_model(
    display_name = "Mercury",
    source_model = base_model,
    epoch_count = 10,
    batch_size =2,
    learning_rate = 0.0001,
    training_data = training_data,
    input_key = 'query',
    output_key = 'response'
)
for status in operation.wait_bar():
    time.sleep(10)

result = operation.result()
print(result)

model = genai.GenerativeModel(model_name='tunedModels name')

snapshots = pd.DataFrame(result.tuning_task.snapshots)
sns.lineplot(data=snapshots, x='epoch', y='mean_loss')

def generate_response(prompt):
    model = genai.GenerativeModel(model_name = result.name)
    response = model.generate_content(prompt)
    return response.text

generate_response('text')