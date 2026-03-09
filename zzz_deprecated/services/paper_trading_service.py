class PaperTrader:

    def __init__(self, starting_balance=10000):
        self.balance = starting_balance
        self.position = 0  # BTC amount
        self.last_price = 0

    def update(self, price, signal):

        self.last_price = price

        # BUY signal
        if signal == "BUY" and self.balance > 0:
            self.position = self.balance / price
            self.balance = 0

        # SELL signal
        elif signal == "SELL" and self.position > 0:
            self.balance = self.position * price
            self.position = 0

        total_equity = self.balance + (self.position * price)

        return {
            "balance_usd": round(self.balance, 2),
            "position_btc": round(self.position, 6),
            "total_equity": round(total_equity, 2)
        }