# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Claude Sonnet 4.6 Chat — Azure AI Foundry
# MAGIC
# MAGIC Uses Claude deployed via Azure AI Foundry (billed through Azure, no separate Anthropic account needed).
# MAGIC
# MAGIC **Prerequisites:** Complete the Azure AI Foundry setup steps below before running.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Azure AI Foundry Setup (one-time)
# MAGIC
# MAGIC ### Step 1 — Deploy Claude in Azure AI Foundry
# MAGIC 1. Go to **https://ai.azure.com** and sign in
# MAGIC 2. Create a **Hub** and **Project** (or use an existing project)
# MAGIC 3. In the left sidebar click **Model catalog**
# MAGIC 4. Search for **"Claude"** → select **Claude Sonnet 4.6** (by Anthropic)
# MAGIC 5. Click **Deploy** → choose **Serverless API** (pay-per-token, no VM needed)
# MAGIC 6. Accept the terms → click **Subscribe and deploy**
# MAGIC 7. Once deployed, go to **Models + endpoints** → click your deployment
# MAGIC 8. Copy the **Target URI** and **Key 1** — you'll need these below
# MAGIC
# MAGIC ### Step 2 — Store the key in Databricks Secrets (recommended)
# MAGIC Run this once in your terminal or Databricks CLI:
# MAGIC ```bash
# MAGIC databricks secrets create-scope azure-ai
# MAGIC databricks secrets put-secret --scope azure-ai --key foundry-key
# MAGIC # paste your Key 1 when prompted
# MAGIC ```
# MAGIC
# MAGIC ### Step 3 — Run this notebook

# COMMAND ----------

# Install Azure AI Inference SDK
%pip install azure-ai-inference -q

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage, AssistantMessage
from azure.core.credentials import AzureKeyCredential

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration
# MAGIC
# MAGIC Paste your Azure AI Foundry endpoint URI below, then either:
# MAGIC - Use the Databricks widget to enter your key temporarily, OR
# MAGIC - Use `dbutils.secrets.get()` (recommended for production)

# COMMAND ----------

# Your Azure AI Foundry endpoint URI (from Models + endpoints in ai.azure.com)
# Example: "https://your-deployment.eastus.models.ai.azure.com"
AZURE_ENDPOINT = "https://YOUR-DEPLOYMENT-NAME.eastus.models.ai.azure.com"

# Option A: Widget (temporary, good for testing)
dbutils.widgets.text("azure_ai_key", "", "Azure AI Foundry Key")
api_key = dbutils.widgets.get("azure_ai_key")

# Option B: Databricks Secrets (recommended for production — comment out Option A above)
# api_key = dbutils.secrets.get(scope="azure-ai", key="foundry-key")

if not api_key:
    raise ValueError("Enter your Azure AI Foundry key in the widget above.")

client = ChatCompletionsClient(
    endpoint=AZURE_ENDPOINT,
    credential=AzureKeyCredential(api_key),
)

print("Azure AI Foundry client ready.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Quick Test

# COMMAND ----------

response = client.complete(
    messages=[
        SystemMessage("You are a helpful assistant."),
        UserMessage("What is Azure Databricks best used for?"),
    ],
    model="claude-sonnet-4-6",
    max_tokens=512,
)

print(response.choices[0].message.content)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ask Any Question

# COMMAND ----------

def ask_claude(question: str, system_prompt: str = "You are a helpful assistant.") -> str:
    """Send a single question to Claude via Azure AI Foundry."""
    response = client.complete(
        messages=[
            SystemMessage(system_prompt),
            UserMessage(question),
        ],
        model="claude-sonnet-4-6",
        max_tokens=2048,
    )
    return response.choices[0].message.content


# Change this and re-run the cell
MY_QUESTION = "Explain Delta Lake in simple terms."

answer = ask_claude(MY_QUESTION)
print(answer)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Multi-Turn Chat Session
# MAGIC Run the chat cells in sequence — Claude remembers the conversation context.

# COMMAND ----------

class ChatSession:
    """Multi-turn conversation with Claude via Azure AI Foundry."""

    def __init__(self, system_prompt: str = "You are a helpful data engineering assistant."):
        self.history = []
        self.system_prompt = system_prompt

    def chat(self, user_message: str) -> str:
        self.history.append(UserMessage(user_message))

        response = client.complete(
            messages=[SystemMessage(self.system_prompt)] + self.history,
            model="claude-sonnet-4-6",
            max_tokens=2048,
        )

        reply = response.choices[0].message.content
        self.history.append(AssistantMessage(reply))

        print(f"You: {user_message}\n")
        print(f"Claude: {reply}\n")
        print("-" * 60)
        return reply

    def reset(self):
        self.history = []
        print("Conversation reset.")


session = ChatSession()

# COMMAND ----------

# First question
session.chat("How do I handle schema evolution in Delta Lake?")

# COMMAND ----------

# Follow-up — Claude remembers the previous question
session.chat("Show me a PySpark code example for that.")

# COMMAND ----------

# Another follow-up
session.chat("What about merging with schema changes?")

# COMMAND ----------

# Start fresh
session.reset()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Data Assistant — Ask About Your Tables

# COMMAND ----------

# Describe your schema and ask Claude to help write queries
MY_SCHEMA = """
Table: sales.transactions
Columns:
  - transaction_id STRING
  - customer_id STRING
  - product_id STRING
  - amount DOUBLE
  - transaction_date DATE
  - region STRING
"""

data_session = ChatSession(
    system_prompt=f"You are a SQL and PySpark expert. The user's data schema:\n{MY_SCHEMA}"
)

data_session.chat("Find the top 5 regions by revenue in the last 90 days, written as a PySpark DataFrame query.")

# COMMAND ----------

data_session.chat("Now convert that to a Spark SQL query I can run in a notebook cell.")
