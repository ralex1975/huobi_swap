from api.huobi.huobi_request_spot import HuobiRequestSpot
from utils.tools import round_to
import datetime
from enum import Enum
from api.model.tasks import LoopRunTask
from utils.config import config


class OrderStatus(Enum):
    SUBMITTED = "submitted"  # 已提交
    PARTIALFILLED = "partial-filled"  # 部分成交
    PARTIALCANCELED = "partial-canceled"  # 部分成交撤销,
    FILLED = "filled"  # 完全成交
    CANCELED = "canceled"  # 已撤销
    CREATED = "created"  # 已提交


class OrderType(Enum):
    BUYMARKET = "buy-market"
    SELLMARKET = "sell-market"
    BUYLIMIT = "buy-limit"
    SELLLIMIT = "sell-limit"
    BUYIOC = "buy-ioc"
    SELLIOC = " sell-ioc"
    BUYLIMITMAKER = "buy-limit-maker"
    SELLLIMITMAKER = "sell-limit-maker"
    BUYSTOPLIMIT = "buy-stop-limit"
    SELLSTOPLIMIT = "sell-stop-limit"
    BUYLIMITFOK = "buy-limit-fok"
    SELLLIMITFOK = "sell-limit-fok"
    BUYSTOPLIMITFOK = "buy-stop-limit-fok"
    SELLSTOPLIMITFOK = "sell-stop-limit-fok"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class SpotGridStrategy(object):

    def __init__(self):
        self.host = config.accounts.get("host", "https://api.huobi.pro")
        self.access_key = config.accounts.get("access_key")
        self.secret_key = config.accounts.get("secret_key")
        self.symbol = config.markets.get("mark_symbol")
        self.gap_percent = config.markets.get("gap_percent", 0.003)  # 网格大小
        self.quantity = config.markets.get("quantity", 1)   # 成交量
        self.quantity_rate = config.markets.get("quantity_rate", 1)   #
        self.min_price = config.markets.get("quantity_rate", 0.0001)  # 价格保留小数位
        # self.min_qty = 0.01  # 数量保留小数位
        self.max_orders = config.markets.get("max_orders", 1)
        self.http_client = HuobiRequestSpot(host=self.host, access_key=self.access_key, secret_key=self.secret_key)
        self.buy_orders = []  # 买单
        self.sell_orders = []  # 卖单
        self.account_id = None

        LoopRunTask.register(self.grid_trader, 10)

    async def init_data(self):
        success, error = await self.http_client.get_accounts()
        if error:
            print("init account_id error. error:", error)
            exit(0)
        if success.get("status") == "ok":
            data = success.get("data")
            for d in data:
                if d.get("type") == "spot":
                    self.account_id = d.get("id")
        if not self.account_id:
            print("init account_id error. msg:", success)
            exit(0)
        order_data, error = await self.http_client.get_open_orders(account_id=self.account_id, symbol=self.symbol)
        if error:
            print("init get_open_orders error. msg:", error)
        if order_data:
            open_orders = order_data.get["data"]
            for order in open_orders:
                order_id = order["id"]
                await self.http_client.cancel_order(order_id)
        symbols_data, err = await self.http_client.get_symbols()
        if err:
            print("get_symbols error. error:", err)
        if symbols_data:
            symbols = symbols_data.get("data")
            for symbol_info in symbols:
                if symbol_info["symbol"] == self.symbol:
                    self.quantity = symbol_info["min-order-amt"] * self.quantity_rate
                    self.min_price = 1 / (10 ** symbol_info["value-precision"])

    async def get_bid_ask_price(self):
        ticker, error = await self.http_client.get_ticker(self.symbol)
        bid_price = 0
        ask_price = 0
        if error:
            print("init get_bid_ask_price error. error:", error)
            return
        if ticker.get("status") == "ok":
            data = ticker.get("tick")
            if data:
                bid_price = float(data.get('bid', [0, 0])[0])
                ask_price = float(data.get('ask', [0, 0])[0])
        else:
            print("get_ticker error.", ticker)
        return bid_price, ask_price

    async def grid_trader(self, *args, **kwargs):
        """
        执行核心逻辑，网格交易的逻辑.
        :return:
        """
        if not self.account_id:
            await self.init_data()
        bid_price, ask_price = await self.get_bid_ask_price()
        print(f"bid_price: {bid_price}, ask_price: {ask_price}")

        # quantity = round_to(float(self.quantity), float(self.min_qty))
        quantity = self.quantity

        self.buy_orders.sort(key=lambda x: float(x['price']), reverse=True)  # 最高价到最低价.
        self.sell_orders.sort(key=lambda x: float(x['price']), reverse=True)  # 最高价到最低价.
        print(f"buy orders: {self.buy_orders}")
        print("------------------------------")
        print(f"sell orders: {self.sell_orders}")

        buy_delete_orders = []  # 需要删除买单
        sell_delete_orders = []  # 需要删除的卖单

        # 买单逻辑,检查成交的情况.
        for buy_order in self.buy_orders:
            check_order, error = await self.http_client.get_order(buy_order.get("id"))
            if check_order:
                if check_order.get('status') == OrderStatus.CANCELED.value:
                    buy_delete_orders.append(buy_order)
                    print(f"buy order status was canceled: {check_order.get('status')}")
                elif check_order.get('status') == OrderStatus.FILLED.value:
                    # 买单成交，挂卖单.
                    print(f"买单成交时间: {datetime.now()}, 价格: {check_order.get('price')}, 数量: {check_order.get('origQty')}")
                    sell_price = round_to(float(check_order.get("price")) * (1 + float(self.gap_percent)), float(self.min_price))
                    if 0 < sell_price < ask_price:
                        # 防止价格
                        sell_price = round_to(ask_price, float(self.min_price))
                    new_sell_order = await self.place_order(type=OrderType.SELLLIMIT, amount=quantity, price=sell_price)
                    if new_sell_order:
                        buy_delete_orders.append(buy_order)
                        self.sell_orders.append(new_sell_order)
                    buy_price = round_to(float(check_order.get("price")) * (1 - float(self.gap_percent)), self.min_price)
                    if buy_price > bid_price > 0:
                        buy_price = round_to(buy_price, float(self.min_price))
                    new_buy_order = await self.place_order(type=OrderType.BUYLIMIT, amount=quantity, price=buy_price)
                    if new_buy_order:
                        self.buy_orders.append(new_buy_order)
                elif check_order.get('status') == OrderStatus.SUBMITTED.value or check_order.get('status') == OrderStatus.CREATED.value:
                    print("buy order status is: New")
                else:
                    print(f"buy order status is not above options: {check_order.get('status')}")

        # 过期或者拒绝的订单删除掉.
        for delete_order in buy_delete_orders:
            self.buy_orders.remove(delete_order)

        # 卖单逻辑, 检查卖单成交情况.
        for sell_order in self.sell_orders:
            check_order, error = self.http_client.get_order(sell_order.get('id'))
            if check_order:
                if check_order.get('status') == OrderStatus.CANCELED.value:
                    sell_delete_orders.append(sell_order)
                    print(f"sell order status was canceled: {check_order.get('status')}")
                elif check_order.get('status') == OrderStatus.FILLED.value:
                    print(
                        f"卖单成交时间: {datetime.now()}, 价格: {check_order.get('price')}, 数量: {check_order.get('origQty')}")
                    # 卖单成交，先下买单.
                    buy_price = round_to(float(check_order.get("price")) * (1 - float(self.gap_percent)), float(self.min_price))
                    if buy_price > bid_price > 0:
                        buy_price = round_to(buy_price, float(self.min_price))
                    new_buy_order = await self.place_order(type=OrderType.BUYLIMIT, amount=quantity, price=buy_price)
                    if new_buy_order:
                        sell_delete_orders.append(sell_order)
                        self.buy_orders.append(new_buy_order)

                    sell_price = round_to(float(check_order.get("price")) * (1 + float(self.gap_percent)), float(self.min_price))
                    if 0 < sell_price < ask_price:
                        # 防止价格
                        sell_price = round_to(ask_price, float(self.min_price))
                    new_sell_order = await self.place_order(type=OrderType.SELLLIMIT, amount=quantity, price=sell_price)
                    if new_sell_order:
                        self.sell_orders.append(new_sell_order)

                elif check_order.get('status') == OrderStatus.SUBMITTED.value or check_order.get('status') == OrderStatus.CREATED.value:
                    print("sell order status is: New")
                else:
                    print(f"sell order status is not in above options: {check_order.get('status')}")

        # 过期或者拒绝的订单删除掉.
        for delete_order in sell_delete_orders:
            self.sell_orders.remove(delete_order)

        # 没有买单的时候.
        if len(self.buy_orders) <= 0:
            if bid_price > 0:
                price = round_to(bid_price * (1 - float(self.gap_percent)), float(self.min_price))
                buy_order = await self.place_order(type=OrderType.BUYLIMIT, amount=quantity, price=price)
                if buy_order:
                    self.buy_orders.append(buy_order)
        elif len(self.buy_orders) > int(self.max_orders): # 最多允许的挂单数量.
            # 订单数量比较多的时候.
            self.buy_orders.sort(key=lambda x: float(x['price']), reverse=False)  # 最低价到最高价
            delete_order = self.buy_orders[0]
            order, error = await self.http_client.cancel_order(delete_order.get('id'))
            if order:
                self.buy_orders.remove(delete_order)

        # 没有卖单的时候.
        if len(self.sell_orders) <= 0:
            if ask_price > 0:
                price = round_to(ask_price * (1 + float(self.gap_percent)), float(self.min_price))
                order = await self.place_order(type=OrderType.SELLLIMIT, amount=quantity, price=price)
                if order:
                    self.sell_orders.append(order)
        elif len(self.sell_orders) > int(self.max_orders):  # 最多允许的挂单数量.
            # 订单数量比较多的时候.
            self.sell_orders.sort(key=lambda x: x['price'], reverse=True)  # 最高价到最低价
            delete_order = self.sell_orders[0]
            order, error = await self.http_client.cancel_order(delete_order.get('id'))
            if order:
                self.sell_orders.remove(delete_order)

    async def place_order(self, type, amount, price):
        success, error = await self.http_client.place_order(account_id=self.account_id, symbol=self.symbol, type=type.value, amount=amount, price=price)
        if error:
            print("place_order error. error:", error)
        if success:
            if success.get("data"):
                order, error1 = await self.http_client.get_order(success.get("data").get("data"))
                if error1:
                    print("get_order error. error:", error1)
                if order:
                    return order.get("data")
        return None