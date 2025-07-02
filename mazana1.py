import asyncio
import json
import logging
import websockets
from statistics import mean

# ------------------------- CONFIGURATION -------------------------

CONFIG = {
    "APP_ID": 71130,
    "INITIAL_STAKE": 0.35,
    "MARTINGALE_MULTIPLIER": 3,
    "GRANULARITY": 1800,  # 30 minutes
    "MIN_CANDLES_REQUIRED": 30,
    "VOLATILITY_THRESHOLD": 0.5,
    "SYMBOLS": ["R_10", "R_25", "R_50", "R_75", "R_100"],
    "SYMBOL_MULTIPLIERS": {
        "R_10": 1.0,
        "R_25": 0.8,
        "R_50": 0.6,
        "R_75": 0.5,
        "R_100": 0.3
    }
}

# ------------------------- ACCOUNTS CONFIG -------------------------

ACCOUNTS = [
    {"token": "REzKac9b5BR7DmF", "role": "master"},
    {"token": "TOKEN_FOLLOWER1", "role": "follower"},
    {"token": "TOKEN_FOLLOWER2", "role": "follower"},
]

# ------------------------- LOGGING -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ------------------------- SINGLE ACCOUNT BOT -------------------------

class SymbolSingleAccount:
    def __init__(self, symbol, token):
        self.symbol = symbol
        self.token = token
        self.ws = None
        self.balance = 0

    async def connect(self):
        try:
            self.ws = await websockets.connect(
                f"wss://ws.derivws.com/websockets/v3?app_id={CONFIG['APP_ID']}"
            )
            await self.send({"authorize": self.token})
            response = await self.recv()
            if "error" in response:
                logging.error(f"[{self.symbol}] Auth failed: {response['error'].get('message')} | Token: {self.token[:5]}...")
                return False
            self.balance = float(response['authorize']['balance'])
            logging.info(f"✅ [{self.symbol}] Connected | Balance: {self.balance:.2f} USD | Token: {self.token[:5]}...")
            return True
        except Exception as e:
            logging.error(f"[{self.symbol}] Connection error: {e}")
            return False

    async def send(self, data):
        await self.ws.send(json.dumps(data))

    async def recv(self):
        response = json.loads(await self.ws.recv())
        return response

    async def execute_trade(self, signal, stake_amount):
        try:
            stake_amount *= CONFIG["SYMBOL_MULTIPLIERS"].get(self.symbol, 1.0)
            await self.send({
                "proposal": 1,
                "amount": round(stake_amount, 2),
                "basis": "stake",
                "contract_type": signal,
                "currency": "USD",
                "duration": 60,
                "duration_unit": "m",
                "symbol": self.symbol
            })
            proposal_response = await self.recv()
            proposal_id = proposal_response.get("proposal", {}).get("id")
            if not proposal_id:
                logging.error(f"[{self.symbol}] Proposal failed | Token: {self.token[:5]}...")
                return

            await self.send({"buy": proposal_id, "price": round(stake_amount, 2)})
            buy_response = await self.recv()
            contract_id = buy_response.get("buy", {}).get("contract_id")
            if not contract_id:
                logging.error(f"[{self.symbol}] Buy failed | Token: {self.token[:5]}...")
                return

            logging.info(f"📊 [{self.symbol}] Trade sent on {self.token[:5]}... | Signal: {signal} | Stake: ${stake_amount:.2f}")

            await asyncio.sleep(125)

            await self.send({"proposal_open_contract": 1, "contract_id": contract_id})
            result_response = await self.recv()
            contract_info = result_response.get("proposal_open_contract", {})
            profit = float(contract_info.get("profit", 0))

            if profit > 0:
                logging.info(f"✅ [{self.symbol}] WIN on {self.token[:5]}... | Profit: ${profit:.2f}")
            else:
                logging.info(f"❌ [{self.symbol}] LOSS on {self.token[:5]}... | Loss: ${abs(profit):.2f}")
        except Exception as e:
            logging.error(f"[{self.symbol}] Trade execution error: {e}")

    async def close(self):
        if self.ws:
            await self.ws.close()

# ------------------------- MASTER BOT -------------------------

class MasterBot(SymbolSingleAccount):
    def __init__(self, symbol, token):
        super().__init__(symbol, token)
        self.martingale_step = 0

    async def get_candles(self):
        await self.send({
            "ticks_history": self.symbol,
            "end": "latest",
            "count": CONFIG["MIN_CANDLES_REQUIRED"],
            "granularity": CONFIG["GRANULARITY"],
            "style": "candles"
        })
        candles_response = await self.recv()
        candles = candles_response.get("candles", [])
        return candles

    def analyze_signal(self, candles):
        if len(candles) < CONFIG["MIN_CANDLES_REQUIRED"]:
            logging.info(f"[{self.symbol}] Not enough candles.")
            return None, None

        body_sizes = [abs(c['close'] - c['open']) for c in candles[-5:]]
        avg_body = mean(body_sizes)
        if avg_body > CONFIG["VOLATILITY_THRESHOLD"]:
            logging.info(f"[{self.symbol}] Market too volatile (avg body {avg_body:.4f}), skipping.")
            return None, None

        closes = [c["close"] for c in candles[-10:]]
        sma = mean(closes)
        current_close = candles[-1]["close"]
        trend = "bullish" if current_close > sma else "bearish"

        body_colors = []
        for candle in candles[-5:]:
            if candle['close'] > candle['open']:
                body_colors.append("green")
            elif candle['close'] < candle['open']:
                body_colors.append("red")
            else:
                body_colors.append("doji")

        trend_color = body_colors[0]
        if all(c == trend_color for c in body_colors[:4]):
            last = body_colors[4]
            if trend_color == last:
                signal = "CALL" if trend_color == "green" else "PUT"
                if (signal == "CALL" and trend == "bullish") or (signal == "PUT" and trend == "bearish"):
                    return signal, 1.0
                else:
                    logging.info(f"[{self.symbol}] Continuation pattern but conflicting with SMA trend, skipping.")
                    return None, None
            else:
                signal = "PUT" if trend_color == "green" else "CALL"
                if (signal == "CALL" and trend == "bullish") or (signal == "PUT" and trend == "bearish"):
                    return signal, 0.5
                else:
                    logging.info(f"[{self.symbol}] Reversal pattern but conflicting with SMA trend, skipping.")
                    return None, None

        logging.info(f"[{self.symbol}] No valid pattern found.")
        return None, None

    async def execute_trade(self, signal, stake_amount):
        try:
            await self.send({
                "proposal": 1,
                "amount": round(stake_amount, 2),
                "basis": "stake",
                "contract_type": signal,
                "currency": "USD",
                "duration": 60,
                "duration_unit": "m",
                "symbol": self.symbol
            })
            proposal_response = await self.recv()
            proposal_id = proposal_response.get("proposal", {}).get("id")
            if not proposal_id:
                logging.error(f"[{self.symbol}] Proposal failed | Token: {self.token[:5]}...")
                return False

            await self.send({"buy": proposal_id, "price": round(stake_amount, 2)})
            buy_response = await self.recv()
            contract_id = buy_response.get("buy", {}).get("contract_id")
            if not contract_id:
                logging.error(f"[{self.symbol}] Buy failed | Token: {self.token[:5]}...")
                return False

            logging.info(f"📊 [{self.symbol}] Trade sent on {self.token[:5]}... | Signal: {signal} | Stake: ${stake_amount:.2f}")

            await asyncio.sleep(125)

            await self.send({"proposal_open_contract": 1, "contract_id": contract_id})
            result_response = await self.recv()
            contract_info = result_response.get("proposal_open_contract", {})
            profit = float(contract_info.get("profit", 0))

            if profit > 0:
                logging.info(f"✅ [{self.symbol}] WIN on {self.token[:5]}... | Profit: ${profit:.2f}")
                self.martingale_step = 0
                return True
            else:
                logging.info(f"❌ [{self.symbol}] LOSS on {self.token[:5]}... | Loss: ${abs(profit):.2f}")
                self.martingale_step += 1
                return False
        except Exception as e:
            logging.error(f"[{self.symbol}] Trade execution error: {e}")
            return False

# ------------------------- MULTI-ACCOUNT MANAGER -------------------------

class MultiAccountBot:
    def __init__(self, accounts, symbol):
        self.symbol = symbol
        self.master_account = None
        self.followers = []
        for acc in accounts:
            if acc["role"] == "master":
                self.master_account = MasterBot(symbol, acc["token"])
            else:
                self.followers.append(SymbolSingleAccount(symbol, acc["token"]))

    async def run(self):
        while True:
            if not await self.master_account.connect():
                await asyncio.sleep(5)
                continue

            candles = await self.master_account.get_candles()
            signal, stake_multiplier = self.master_account.analyze_signal(candles)

            if signal:
                stake_amount = (CONFIG["INITIAL_STAKE"] * 
                                (CONFIG["MARTINGALE_MULTIPLIER"] ** self.master_account.martingale_step) * 
                                stake_multiplier)

                tasks = [self.master_account.execute_trade(signal, stake_amount)]
                for follower in self.followers:
                    if await follower.connect():
                        tasks.append(follower.execute_trade(signal, stake_amount))

                await asyncio.gather(*tasks)

                for follower in self.followers:
                    await follower.close()

            await self.master_account.close()
            await asyncio.sleep(5)

# ------------------------- MAIN -------------------------

async def main():
    bots = []
    for symbol in CONFIG["SYMBOLS"]:
        bot = MultiAccountBot(ACCOUNTS, symbol)
        bots.append(bot.run())

    await asyncio.gather(*bots)

if _name_ == "_main_":
    asyncio.run(main())