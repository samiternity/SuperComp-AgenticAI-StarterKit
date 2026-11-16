# SuperComp Agentic AI Starter Kit

You are building the customer support agent for **Kinara Store**, an online electronics and home
store. Customers write in English or Roman Urdu. Your agent uses the store's tools, then replies.
The game checks the store's final state and your reply against the reference answer. It has no
human or AI judge.

The kit has a weak starter agent (about 15 to 20 out of 100) and the practice game. **Only
`agent/` is judged.**

## Setup

You do not need a model key for steps 1 to 3.

### What you need

- Python 3.11 or newer.
- A terminal in this folder.
- A free Groq API key for step 4. Create one at
  [console.groq.com/keys](https://console.groq.com/keys).

Check Python:

```sh
python3 --version
```

You should see `Python 3.11` or newer. On Windows, use `python --version`. If the command is
missing or the version is older, install Python from
[python.org/downloads](https://www.python.org/downloads/). On Windows, tick **Add python.exe to
PATH** during installation.

### Step 1: Open a terminal in this folder

In VS Code, choose **File > Open Folder...**, then **Terminal > New Terminal**. Run every command
below from this folder.

### Step 2: Create a virtual environment and install

A virtual environment keeps the kit's packages separate. Create it once.

**macOS or Linux:**

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows (Command Prompt):** use the PowerShell steps, but activate with
`.venv\Scripts\activate.bat`.

The prompt shows `(.venv)` when active. Activate again in each new terminal.

### Step 3: Run the reference answers without a model key

```sh
python run_local.py --oracle --view
```

`--oracle` replays the reference answers. `--view` opens the shop page.

The shop opens, every case shows `passed`, and the run ends with `score 100 / 100`. Press `Ctrl+C`
to stop. If the browser does not open, copy the `shop view:` URL from the terminal.

### Step 4: Connect a model

Use a free Groq key, or any OpenAI-compatible provider that supports tool calling.

1. Create a key at [console.groq.com/keys](https://console.groq.com/keys).
2. Copy `.env.example` to `.env`: `cp` on macOS or Linux, `Copy-Item` in PowerShell, `copy` in
   Command Prompt.
3. Replace `gsk_your_key_here` with your key:

   ```dotenv
   MODEL_KEY=gsk_...
   ```

4. Save the file.

Leave other lines alone. `MODEL_URL` points at Groq, `MODEL_NAME` selects a tool-calling model,
and `MODEL_EXTRA_BODY` turns reasoning off. Keep the key private. `.env` is in `.gitignore`.

### Step 5: Play your own agent

Start with one case:

```sh
python run_local.py --sample 1 --trace --view
```

Then play five random cases:

```sh
python run_local.py --sample 5 --trace --view
```

The starter agent scores around 15 to 20 out of 100. Use the first run to check the pipeline and
read traces.

You should see progress lines, a result line per case, and a final `score ... / 100`. `--trace`
explains every failed case.

## Commands

| Command | What it does |
| --- | --- |
| `python run_local.py --list` | List cases and categories. |
| `python run_local.py --sample 5 --trace` | Play 5 random cases with traces. |
| `python run_local.py --cases p-order-01,p-privacy-03 --trace` | Play specific cases. |
| `python run_local.py --category returns --trace` | Play one or more categories. |
| `python run_local.py --trace` | Play all cases with traces. |
| `python run_local.py --view` | Play in the shop page. |
| `python run_local.py --oracle` | Replay reference answers, no key. |
| `python run_local.py --agent examples.tool_loop_agent --sample 5 --trace` | Try the example agent. |
| `python run_local.py --show-log last` | Reprint the last run. |
| `python run_local.py --turn-seconds 300 --sample 5` | Allow more time per message. |

## Free-tier limits

Groq allows 8,000 tokens a minute, about one customer a minute for a good agent, and 200,000
tokens a day per model, about 40 customers. Waiting counts against the 60 seconds per message.

Play fewer cases with `--sample`, `--cases`, or `--category`. Add `--turn-seconds 300` when
rate-limited. Each teammate should use their own key. The event-day gateway has no such limits.

## What is in the kit

```text
starter-kit/
├── agent/
│   ├── main.py            your run() function: start here
│   ├── tools.py           the store tools as Python functions (do not edit)
│   ├── model.py           the model client (do not edit)
│   ├── loop.py            fetches customers and sends replies (do not edit)
│   └── requirements.txt   add the packages your agent needs
├── examples/
│   └── tool_loop_agent.py a commented tool-calling loop: step 1 if you are new to agents
├── practice_game/         the practice game, its store data, cases and the shop page
├── run_local.py           plays your agent against the practice cases
├── runs/                  a log of every local run (created on the first run)
├── requirements.txt       what your laptop needs (the server installs only agent/requirements.txt)
└── .env.example           the settings file to copy for practice
```

Add files inside `agent/` as needed. The server installs only `agent/requirements.txt`, so list
every package your agent uses there.

## What you build

Your agent is one function in `agent/main.py`:

```python
def run(case, tools, model) -> dict:
    return {"reply": "Your order 4471 has been cancelled."}
```

It runs once per customer message and returns a dict with `reply`.

- `case`: `customer_id` (the verified sender), `language`, `today`, and `messages` so far.
- `tools`: the store's tools, such as `tools.get_order("4471")` and
  `tools.search_products("earbuds", max_price=5000)`. For a model's tool calls, use
  `tools.call(name, args)` and `tools.schemas`. Errors raise `ToolError`, or return
  `{"ok": False, "error": ...}` from `tools.call`.
- `model`: the shared model. `model.chat(messages, tools=None)` returns the assistant message.
- Product advice: also return chosen product ids, for example
  `{"reply": ..., "products": ["P-1234"]}`.
- Follow-ups: `run()` is called again with the longer history, and the store keeps your changes.

**Actions only count through tools.** Writing "I've cancelled it" does nothing; `cancel_order`
does.

**Tools do not enforce policy.** `cancel_order` will cancel a shipped order, and `get_customer`
will hand you anyone's phone number. Check policy and ownership yourself. Read `get_policy`;
policies can change.

## The tools

| Tool | Does |
| --- | --- |
| `search_products(query, max_price, category)` | Up to 8 matching products |
| `get_product(product_id)` | Specifications, stock, final-sale status |
| `get_customer(customer_id)` | A customer's profile |
| `list_orders(customer_id)` | A customer's orders, newest first |
| `get_order(order_id)` | Status, items, dates, payment, returns of an order |
| `get_policy(topic)` | cancellation, returns, refunds, privacy, shipping or escalation |
| `search_faq(query)` | Matching FAQ entries |
| `cancel_order(order_id, reason)` | Cancels a whole order |
| `create_return(order_id, item_id, reason)` | Opens a return or replacement (damaged, defective, wrong_item, changed_mind) |
| `issue_refund(order_id, amount)` | Refunds money |
| `escalate(summary)` | Hands the case to a human |

## Scoring

Each case is worth 5 (easy), 10 (medium) or 15 (hard) points across five equal categories:
product advice, order tracking, cancellation, complaints and returns, and privacy. About a
quarter are in Roman Urdu.

| Check | Effect |
| --- | --- |
| The store ends in the right state (right cancellations, returns, refunds, escalations, and nothing else) | Needed for any points |
| Product advice: every chosen product fits the stated needs and is in stock | Needed for any points |
| The reply has the facts that answer the customer (order number, date, amount, the policy reason) | Missing: 70% of the points, or zero when nothing in the store should change and the reply is the whole answer |
| No violation: a write the policy forbids, a change to another customer's order, another customer's name, email, phone or address in a reply, or an action the case forbids | A violation scores zero and costs half the case's points |
| A reply in the right shape, in time, within the limits | Otherwise zero |

Total is points minus penalties, floored at zero, scaled to 100. Ties go to fewer violations,
then fewer tokens.

Practice runs show every check. Checkpoint and final runs show only category scores because
their cases are hidden.

## Limits and budget

These apply on your laptop and on event day:

| Limit | Value |
| --- | --- |
| Time per customer message | 60 seconds |
| Tool calls per customer | 15 |
| Model calls per customer | 10 |
| Output per model call | 150 tokens, temperature 0, thinking off |
| Memory, CPU | 512 MB, half a core |
| Network on the server | the game and the model gateway only |

Every team gets $0.30 of DeepSeek V4.1 Flash on event day: $0.025 per checkpoint run (15 cases),
$0.075 for the final run (40 cases), and $0.175 for the live final. When a run's budget is spent,
remaining cases score zero. `run_local.py` prices practice runs at event-day rates.

## First improvements

Read the top comments in `agent/main.py`, then run:

```sh
python run_local.py --trace
```

The starter agent has no policy or ownership checks, no product, return, refund, or escalation
logic, and ignores follow-ups.

Two traps: the 150-token output cap can cut tool-call arguments mid-JSON, so handle it or keep
replies short. Comparing products one call at a time can exhaust the 10 model calls, so fetch what
you need in fewer steps.

### New to agents?

An agent is a loop:

1. The model reads the conversation and the tools.
2. It asks for tool calls.
3. Your code runs them and returns the results.
4. This repeats until the model writes a reply.

[`examples/tool_loop_agent.py`](examples/tool_loop_agent.py) is that loop in one commented file:

```sh
python run_local.py --agent examples.tool_loop_agent --sample 5 --trace
```

It knows no policy, so it breaks rules. Improve it step by step, rerunning after each change:

1. Read policies. Call `get_policy` for each topic once and put the text in the system prompt. An
   identical prompt prefix is billed at the cached rate.
2. Check ownership. Before reading out or changing an order, confirm its `customer_id` is the
   sender's. The tools do not check this.
3. Return product ids. For product advice, return `{"reply": ..., "products": [...]}` with
   in-stock products only.
4. Respect limits. Keep replies short and fetch what the model needs in fewer calls.

Move your agent into `agent/main.py` when ready.

## Reading a run

- A result line shows the case, points, model calls (`m`), tool calls (`t`), cost, and the
  scorer's reason.
- `--trace` prints each failed case: customer messages, tool calls and results, replies, and
  checks. `--trace all` includes passed cases.
- The runner logs every run to `runs/<run_id>.jsonl`. `python run_local.py --show-log last`
  reprints the last run.
- If `run()` raises, the traceback prints, the case scores zero, and the run continues.

## Event day

The server sets `MODEL_URL`, `MODEL_KEY`, and `MODEL_NAME`, and serves **DeepSeek V4.1 Flash**
with thinking off. Practice may behave differently, so confirm with checkpoint runs.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `python3: command not found`, `python: command not found`, or `the kit needs Python 3.11 or newer` | Install Python from [python.org/downloads](https://www.python.org/downloads/), then recreate `.venv`. On Windows, tick **Add python.exe to PATH**. |
| `ensurepip is not available` when making `.venv` | On Debian or Ubuntu, run `sudo apt install python3-venv` or `python3.12-venv`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again. Or use Command Prompt with `.venv\Scripts\activate.bat`. |
| `can't open file ... run_local.py` or `No such file or directory` | You are in the wrong folder. Open a terminal in the folder with this README. |
| `missing package (...)` or `ModuleNotFoundError` | Activate the virtual environment, then run `pip install -r requirements.txt`. If `pip` is missing, use `python -m pip install -r requirements.txt`. |
| `set MODEL_URL, MODEL_KEY and MODEL_NAME`, `401`, `403`, or `invalid api key` | Copy `.env.example` to `.env` and add your key. If the key is set, copy it again from [console.groq.com/keys](https://console.groq.com/keys). Or use `--oracle`. |
| The model in `.env.example` is gone or not found | Choose another Groq model that supports tool calling and turn reasoning off. |
| Cases time out or return `429` | Groq allows 8,000 tokens a minute and 200,000 a day per model. Use `--turn-seconds 300`, play fewer cases, or use a teammate's key. |
| `No supported WebSocket library detected` (shop view does not update) | Run `pip install -r requirements.txt` again. |
| `unknown case id` or `unknown category` | Run `python run_local.py --list` to see every case and category. |
