import asyncio
import websockets
import json
from datetime import datetime, timezone
import pytz
from rich.console import Console
from rich.table import Table

console = Console()

# --------------------- CONFIG ---------------------
CONFIG = {
    "APP_ID": 76510,
    "TOKEN": "LDG7hjLbnbK6dRu",
    "SYMBOLS": {
        "frxEURUSD": {"stake": 0.5, "type": "financial"},
        "frxGBPUSD": {"stake": 0.5, "type": "financial"},
        "frxUSDJPY": {"stake": 0.5, "type": "financial"},
        "frxXAUUSD": {"stake": 0.35, "type": "commodity"},
        "frxXAGUSD": {"stake": 0.35, "type": "commodity"},
    },
    "MARTINGALE_MULTIPLIER": 3,
    "TRADE_WINDOWS": [
        {"day": "any", "hour": 7, "minute": 0},
        {"day": "any", "hour": 12, "minute": 0},
        {"day": 1, "hour": 1, "minute": 0},
        {"day": "any", "hour": 16, "minute": 0},
    ],
    "MAX_TRADES_PER_SESSION": 2
}

# --------------------- STATE ----------------------
trade_counts = {symbol: 0 for symbol in CONFIG["SYMBOLS"]}
stats = {
    symbol: {"wins": 0, "losses": 0, "PnL": 0.0, "entries": 0}
    for symbol in CONFIG["SYMBOLS"]
}
current_stakes = {symbol: CONFIG["SYMBOLS"][symbol]["stake"] for symbol in CONFIG["SYMBOLS"]}

# ------------------- UTILS ------------------------
def is_trade_window():
    utc_now = datetime.now(timezone.utc)
    gmt3 = pytz.timezone('Etc/GMT-3')  # GMT+3 (Etc/GMT-3 means UTC+3)
    now = utc_now.astimezone(gmt3)
    for window in CONFIG["TRADE_WINDOWS"]:
        if (window["day"] == "any" or window["day"] == now.weekday()) and \
           window["hour"] == now.hour and window["minute"] == now.minute:
            console.print(f"[green]Trade window is OPEN at {now} (GMT+3)[/green]")
            return True
    console.print(f"[yellow]Trade window is CLOSED at {now} (GMT+3)[/yellow]")
    return False

def reset_trade_counts():
    global trade_counts
    trade_counts = {symbol: 0 for symbol in CONFIG["SYMBOLS"]}

async def get_candles(ws, symbol):
    try:
        request = {
            "ticks_history": symbol,
            "count": 50,
            "end": "latest",
            "style": "candles",
            "granularity": 60,
            "subscribe": 0
        }
        await ws.send(json.dumps(request))
        response = await ws.recv()
        return json.loads(response)
    except Exception as e:
        console.print(f"[red][ERROR][/red] Failed to fetch candles for {symbol}: {e}")
        return {}

def analyse(candles):
    c = candles.get("candles", [])
    if len(c) < 2:
        return None
    if c[-2]['open'] > c[-2]['close'] and c[-1]['open'] < c[-1]['close'] and c[-1]['close'] > c[-2]['open']:
        return "CALL"
    if c[-2]['open'] < c[-2]['close'] and c[-1]['open'] > c[-1]['close'] and c[-1]['close'] < c[-2]['open']:
        return "PUT"
    return None

async def place_trade(ws, symbol, stake, direction):
    try:
        request = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "amount": stake,
                "basis": "stake",
                "contract_type": direction,
                "currency": "USD",
                "duration": 1,
                "duration_unit": "m",
                "symbol": symbol
            }
        }
        await ws.send(json.dumps(request))
        console.print(f"[blue][TRADE][/blue] {symbol} {direction} {stake} USD")
        import random
        result = random.choice(["win", "loss"])
        return result
    except Exception as e:
        console.print(f"[red][ERROR][/red] Failed to place trade for {symbol}: {e}")
        return None

def show_stats():
    table = Table(title="CRT BOT STATISTICS", show_lines=True)
    table.add_column("Symbol")
    table.add_column("Entries", justify="right")
    table.add_column("Wins", justify="right")
    table.add_column("Losses", justify="right")
    table.add_column("PnL (USD)", justify="right")
    for symbol, data in stats.items():
        table.add_row(
            symbol,
            str(data["entries"]),
            str(data["wins"]),
            str(data["losses"]),
            f"{data['PnL']:.2f}"
        )
    console.print(table)

def parse_auth_response(auth_json):
    try:
        data = json.loads(auth_json)
        auth_info = data.get("authorize", {})
        account_list = auth_info.get("account_list", [])
        balance = auth_info.get("balance", None)
        currency = auth_info.get("currency", None)
        fullname = auth_info.get("fullname", "Unknown")
        country = auth_info.get("country", "Unknown")
        is_virtual = auth_info.get("is_virtual", None)

        console.print("[bold green]=== Authorization Success ===[/bold green]")
        console.print(f"User: [cyan]{fullname}[/cyan]")
        console.print(f"Country: [cyan]{country}[/cyan]")
        console.print(f"Balance: [cyan]{balance} {currency}[/cyan]")
        console.print(f"Virtual Account: [cyan]{'Yes' if is_virtual else 'No'}[/cyan]")
        console.print(f"Accounts ({len(account_list)}):")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Login ID", style="dim")
        table.add_column("Type")
        table.add_column("Currency")
        table.add_column("Category")
        table.add_column("Virtual")

        for acc in account_list:
            table.add_row(
                acc.get("loginid", "N/A"),
                acc.get("account_type", "N/A"),
                acc.get("currency", "N/A"),
                acc.get("account_category", "N/A"),
                "Yes" if acc.get("is_virtual", 0) else "No"
            )
        console.print(table)
        console.print("="*30)
    except Exception as e:
        console.print(f"[red]Error parsing auth response:[/red] {e}")

async def run_bot():
    uri = f"wss://ws.binaryws.com/websockets/v3?app_id={CONFIG['APP_ID']}"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"authorize": CONFIG["TOKEN"]}))
                auth_response = await ws.recv()
                parse_auth_response(auth_response)
                console.print("[cyan]Miandry fotoana ahafahana miditra amin'ny trade window...[/cyan]")

                while True:
                    if is_trade_window():
                        console.print("[yellow][TRADE WINDOW OPEN][/yellow]")
                        for symbol in CONFIG["SYMBOLS"]:
                            if trade_counts[symbol] >= CONFIG["MAX_TRADES_PER_SESSION"]:
                                continue

                            candles = await get_candles(ws, symbol)
                            if not candles:
                                continue

                            direction = analyse(candles)
                            if direction:
                                stake = current_stakes[symbol]
                                result = await place_trade(ws, symbol, stake, direction)
                                if result is None:
                                    continue

                                trade_counts[symbol] += 1
                                stats[symbol]["entries"] += 1

                                if result == "win":
                                    stats[symbol]["wins"] += 1
                                    stats[symbol]["PnL"] += stake * 0.95
                                    current_stakes[symbol] = CONFIG["SYMBOLS"][symbol]["stake"]
                                else:
                                    stats[symbol]["losses"] += 1
                                    stats[symbol]["PnL"] -= stake
                                    current_stakes[symbol] *= CONFIG["MARTINGALE_MULTIPLIER"]

                                console.print(f"[RESULT] {symbol} - {result.upper()} | PnL: {stats[symbol]['PnL']:.2f} USD")

                        show_stats()
                        await asyncio.sleep(60)
                    else:
                        reset_trade_counts()
                        await asyncio.sleep(20)
        except Exception as e:
            console.print(f"[red][RECONNECT][/red] Connection lost, retrying in 5s... Reason: {e}")
            await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(run_bot())
