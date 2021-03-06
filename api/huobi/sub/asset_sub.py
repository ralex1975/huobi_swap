from api.huobi.sub.base_sub import BaseSub
from utils import tools
from utils import logger


class AssetSub(BaseSub):
    """
    资产变动订阅
    """

    def __init__(self, symbol, asset):
        """
        交割合约symbol:btc、bch
        永久合约合约symbol:BTC-USD
        """
        self._symbol = symbol
        self._asset = asset
        self._ch = "accounts.{symbol}".format(symbol=self._symbol)

    def ch(self):
        return self._ch

    def symbol(self):
        return self._symbol

    def sub_data(self):
        data = {
            "op": "sub",
            "cid": tools.get_uuid1(),
            "topic": self._ch
        }
        return data

    async def call_back(self, op, data):
        assets = {}
        for item in data["data"]:
            symbol = item["symbol"].upper()
            total = float(item["margin_balance"])
            free = float(item["margin_available"])
            locked = float(item["margin_frozen"])
            risk = item["risk_rate"]
            rate = item["lever_rate"]
            factor = item["adjust_factor"]
            liquidation = item["liquidation_price"]
            if total > 0:
                assets[symbol] = {
                    "total": "%.8f" % total,
                    "free": "%.8f" % free,
                    "locked": "%.8f" % locked,
                    "risk": risk,
                    "rate": rate,
                    "liquidation": liquidation,
                    "factor": factor
                }
        if assets == self._asset.assets:
            update = False
        else:
            update = True
        self._asset.update = update
        self._asset.assets = assets
        self._asset.timestamp = data["ts"]
        logger.info("update assets:", self._asset.__str__(), caller=self)


