import copy
from utils import tools
from utils import logger
import pandas as pd
import numpy as np
from strategy.base_strategy import BaseStrategy
import talib
from api.model.error import Error
from api.model.tasks import SingleTask
from utils.tools import round_to
import math
from collections import deque


class QuantificationStrategy(BaseStrategy):
    """
    网格策略
    """

    def __init__(self):
        self.price_margin = []  # 设置网格价格
        self.position_weight = []  # 设置网格的仓位
        self.position_weight_label = []  # 设置网格仓位标签
        self.band = None  # 网格价格
        self.atr = 0  # 真实波幅
        self.atr_per = 0.05   # 最小网格高度要求
        self.min_index = -1  # 网格基准先位置
        self.grids = deque(maxlen=10)  # 记录价格在网格中的位置
        self.close_position_rate = 5  # 平仓价格倍数
        self.margin_num_limit = 4  # 最少网格要求
        super(QuantificationStrategy, self).__init__()

    def reset_bank(self, df):
        reset = True
        # if self.band is None:
        #     reset = True
        # else:
        #     open_orders = copy.copy(self.orders)
        #     position = copy.copy(self.position)
        #     if len(open_orders) == 0 and position.short_quantity == 0 and position.long_quantity == 0:
        #         reset = True
        if reset:
            self.atr = 0
            df["atr"] = talib.ATR(df["high"], df["low"], df["close"], 20)
            df["max_high"] = talib.MAX(df["high"], self.klines_max_size)
            df["min_low"] = talib.MIN(df["low"], self.klines_max_size)
            current_bar = df.iloc[-1]
            atr = current_bar["atr"]
            if current_bar["close"] * self.atr_per / self.lever_rate > atr:  # 网格高度达不到最小要求
                return
            num = math.floor((current_bar["max_high"] - current_bar["min_low"])/atr)
            if num < self.margin_num_limit:  # 网格太少
                return
            self.atr = atr
            if num % 2 == 0:
                num = num + 1
            self.min_index = math.floor(num/2)
            self.grids.clear()
            self.price_margin = []
            self.position_weight = []
            self.position_weight_label = []
            self.price_margin.append(round_to((-(self.min_index + self.close_position_rate) * self.atr), self.price_tick))
            for i in range(0, num):
                self.price_margin.append(round_to((i - self.min_index) * self.atr, self.price_tick))
            self.price_margin.append(round_to(((self.min_index + self.close_position_rate) * self.atr), self.price_tick))
            # df['olhc'] = df[["open", "close", "high", "low"]].mean(axis=1)
            self.band = np.mean(df['close']) + np.array(self.price_margin) * np.std(df['close']) # 计算各个网格的价格
            if self.trading_curb == "long":  # 做多的情况 计算网格仓位
                for i in range(0, num):
                    if i == 0:
                        self.position_weight.append(num - 1)
                    if i == num - 1:
                        self.position_weight.append(0)
                    else:
                        self.position_weight.append(num - i - 1)
                    self.position_weight_label.append(i)
            elif self.trading_curb == "short":  # 做空的情况 计算网格仓位
                for i in range(0, num):
                    if i == 0:
                        self.position_weight.append(0)
                    if i == num - 1:
                        self.position_weight.append(num - 1)
                    else:
                        self.position_weight.append(i + 1)
                    self.position_weight_label.append(i)
            else:  # 向上做空向下做多的情况 计算网格仓位
                for i in range(0, num):
                    if i == self.min_index:
                        self.position_weight.append(0)
                    index = i
                    if i == 0:
                        index = 1
                    elif i == num - 1:
                        index = num - 2
                    self.position_weight.append(abs(self.min_index - index))
                    self.position_weight_label.append(i)

            self.position_weight_label.append(num)

    def calculate_signal(self):
        self.long_status = 0
        self.short_status = 0
        self.short_trade_size = 0
        self.long_trade_size = 0
        klines = copy.copy(self.klines)
        position = copy.copy(self.position)
        df = klines.get("market." + self.mark_symbol + ".kline." + self.period)
        self.reset_bank(df)
        if self.atr == 0:
            return
        current_bar = df.iloc[-1]
        grid = -2
        if current_bar["close"] <= self.band[0]:
            grid = -1
        elif current_bar["close"] >= self.band[-1]:
            grid = len(self.band)
        else:
            grid = pd.cut([current_bar["close"]], self.band, labels=self.position_weight_label)[0]

        print(self.min_index)
        print(self.price_margin)
        print(self.position_weight)
        print(self.position_weight_label)
        print(self.band)
        print(current_bar["close"], grid)

        if len(self.grids) == 0:
            self.grids.append(grid)
        if grid == -1 and grid == len(self.band):  # 平仓
            self.long_status = -1  # 平多
            self.short_status = -1  # 平空
        else:
            if len(self.grids) == 1:  # 补仓
                if self.trading_curb == "long":  # 开多仓
                    self.long_status = 1
                    self.long_trade_size = self.position_weight[grid]
                elif self.trading_curb == "short":  # 开空仓
                    self.short_status = 1
                    self.short_trade_size = self.position_weight[grid]
                else:
                    if grid < self.min_index:  # 在基准线下方做多
                        self.long_status = 1
                        self.long_trade_size = self.position_weight[grid]
                    else:  # 基准线上方做空
                        self.short_status = 1
                        self.short_trade_size = self.position_weight[grid]
                return
            if self.grids[-1] == grid:
                return
            if self.grids[-2] < self.grids[-1]:  # 向上
                if self.trading_curb == "long":  # 平多仓
                    if grid > 0:
                        grid = grid - 1
                    if position.long_quantity > self.position_weight[grid]:
                        self.long_status = 1
                        self.long_trade_size = self.position_weight[grid]
                elif self.trading_curb == "short":  # 加空仓
                    self.short_status = 1
                    self.short_trade_size = self.position_weight[grid]
                else:
                    if grid < self.min_index: # 平多仓
                        if grid > 0:
                            grid = grid - 1
                        if position.long_quantity > self.position_weight[grid]:
                            self.long_status = 1
                            self.long_trade_size = self.position_weight[grid]
                    if grid > self.min_index:  # 加空仓
                        self.short_status = 1
                        self.short_trade_size = self.position_weight[grid]

            if self.grids[-2] > self.grids[-1]:  # 向下
                if self.trading_curb == "long":  # 加多仓
                    self.long_status = 1
                    self.long_trade_size = self.position_weight[grid]
                elif self.trading_curb == "short":  # 平空仓
                    if grid < len(self.band - 2):
                        grid = grid + 1
                    if position.long_quantity > self.position_weight[grid]:
                        self.short_status = 1
                        self.short_trade_size = self.position_weight[grid]
                else:
                    if grid < self.min_index:  # 加多仓
                        self.long_status = 1
                        self.long_trade_size = self.position_weight[grid]
                    if grid > self.min_index:  # 平空仓
                        if grid < len(self.band - 2):
                            grid = grid + 1
                        if position.long_quantity > self.position_weight[grid]:
                            self.short_status = 1
                            self.short_trade_size = self.position_weight[grid]






