# SuperComp Agentic AI Starter Kit

Build a customer support agent for our store. Customers write in English or Roman Urdu. The game scores your replies and checks what changed in the store. Only `agent/` is judged.

## Agentic AI Track Resources

- **Video Tutorial:** [Watch on YouTube](https://www.youtube.com/watch?v=KXcWqLZoxyw)
- **Registration & Details:** [SuperComp Website](https://supercomp.bucis.org/tracks/agentic-ai)
- **Rule Book:** [Download PDF](https://supercomp.bucis.org/downloads/rulebook-agentic-ai.pdf)

## Setup

Use Python 3.11 or newer. If you need to install it, download it from [python.org](https://www.python.org/downloads/). 

### Step 1. Install dependencies

Open a terminal in the folder containing this README.

**macOS or Linux**

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows Command Prompt**

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```



### Step 2. Check the practice game

```sh
python run_local.py --oracle --view
```

This replays the reference answers without a model key. This command will open a page in your browser. Expect `score 100 / 100`. 

### Step 3. Connect a model

Rename `.env.example` file to `.env`. Use your Groq API key and a model ID that supports tool calling. [Get a Groq key](https://console.groq.com/keys).

```dotenv
MODEL_URL=https://api.groq.com/openai/v1
MODEL_KEY=gsk_your_key_here
MODEL_NAME=your_tool_calling_model_id
```

For another OpenAI-compatible provider, use its URL, key, and model ID.

### Step 4. Run the starter agent

```sh
python run_local.py --sample 5 --trace --view
```


## Try the example agent

```sh
python run_local.py --agent examples.tool_loop_agent --sample 5 --trace --view
```

Open [examples/tool_loop_agent.py](examples/tool_loop_agent.py) to follow the loop. The model reads the conversation and requests tools. The example runs those tools and sends the results back to the model until it writes a reply.

Use the example as a starting point for `agent/main.py`. Read store rules with `get_policy` before taking action, and verify that an order belongs to the sender before sharing details or changing it. The store tools execute actions and leave policy and ownership checks to your agent.

## Build your agent

The runner calls `run(case, tools, model)` once per customer message. On follow-ups, `case["messages"]` includes the conversation so far, and the store keeps changes from earlier turns. Use `case["customer_id"]` to identify the verified sender.

```python
def run(case, tools, model) -> dict:
    return {"reply": "Your order 4471 has been cancelled."}
```

Call the appropriate store tool to carry out any action you confirm in the reply. For product advice, also return the selected in-stock product IDs in `products`. List any extra dependencies in `agent/requirements.txt`.

Each customer message has a 60-second limit. Each customer case allows 15 tool calls and 10 model calls, with up to 150 output tokens per model call.

## Scoring

Easy, medium, and hard cases are worth 5, 10, and 15 points. Full credit requires the expected store state, the required facts in the reply, and valid product IDs in product advice cases.

- Missing reply facts earn 70% if the required store changes or product choices are correct. If the reply is the whole answer, missing facts earn zero.
- Incorrect store changes or product choices, crashes, and timeouts earn zero.
- A policy or privacy violation earns zero and deducts half the case's point value from the run total.

The final score is the percentage of available points earned after penalties, with a minimum of zero.

## Practice commands

| Command | What it does |
| --- | --- |
| `python run_local.py --list` | List cases and categories. |
| `python run_local.py --cases p-order-01,p-privacy-03 --trace` | Run specific cases. |
| `python run_local.py --category returns --trace` | Run a category. |
| `python run_local.py --trace` | Run all practice cases and trace failures. |
| `python run_local.py --show-log last` | Reprint the last run. |

