import asyncio
import math
import logging
from binance import AsyncClient
from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
import warnings
import pandas as pd
import os

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальна змінна для клієнта, який буде ініціалізований пізніше
client = None
symbol = 'BTCUSDT'
entry_threshold = 60  # Поріг для відкриття позиції
leverage = 10  # Кредитне плече

# Функція для розрахунку ваги індикаторів
def calculate_indicator_weights(data):
    conditions_met = 0

    # RSI
    if data['RSI'].iloc[-1] < 20:
        conditions_met += 10  # Перепроданість, можливий лонг
        logger.info("RSI < 20: +10%")
    elif data['RSI'].iloc[-1] > 80:
        conditions_met -= 10  # Перекупленість, можливий шорт
        logger.info("RSI > 80: -10%")

    # Перетин MA7 і MA25
    if data['MA7'].iloc[-1] > data['MA25'].iloc[-1]:
        conditions_met += 20  # MA7 перетинає MA25 вгору, можливий лонг
        logger.info("MA7 перетинає MA25 вгору: +20%")
    elif data['MA7'].iloc[-1] < data['MA25'].iloc[-1]:
        conditions_met -= 20  # MA7 перетинає MA25 вниз, можливий шорт
        logger.info("MA7 перетинає MA25 вниз: -20%")

    # Напрямок і сила тренду MA7
    ma7_trend = data['MA7'].diff().iloc[-1]
    if ma7_trend > 0:
        conditions_met += min(15, abs(ma7_trend))  # Вага тренду вгору
        logger.info(f"Напрямок тренду MA7 вгору: +{min(15, abs(ma7_trend))}%")
    elif ma7_trend < 0:
        conditions_met -= min(15, abs(ma7_trend))  # Вага тренду вниз
        logger.info(f"Напрямок тренду MA7 вниз: -{min(15, abs(ma7_trend))}%")

    # MACD
    if data['MACD'].iloc[-1] > data['MACD_Signal'].iloc[-1]:
        conditions_met += 15  # MACD вище сигналу, можливий лонг
        logger.info("MACD вище сигналу: +15%")
    elif data['MACD'].iloc[-1] < data['MACD_Signal'].iloc[-1]:
        conditions_met -= 15  # MACD нижче сигналу, можливий шорт
        logger.info("MACD нижче сигналу: -15%")

    # Смуги Боллінджера
    if data['Close'].iloc[-1] > data['UpperBand'].iloc[-1]:
        conditions_met -= 10  # Ціна вище верхньої смуги Боллінджера, можливий розворот вниз
        logger.info("Ціна вище верхньої смуги Боллінджера: -10%")
    elif data['Close'].iloc[-1] < data['LowerBand'].iloc[-1]:
        conditions_met += 10  # Ціна нижче нижньої смуги Боллінджера, можливий розворот вгору
        logger.info("Ціна нижче нижньої смуги Боллінджера: +10%")

    return conditions_met

# Функція для розрахунку стоплосу та тейкпрофіту
def calculate_stop_loss_take_profit(data_30m, data_1h, entry_price, side):
    # Розрахунок середнього ATR для 30-хвилинного та 1-годинного таймфреймів
    atr_30m = data_30m['ATR'].iloc[-1]
    atr_1h = data_1h['ATR'].iloc[-1]
    average_atr = (atr_30m + atr_1h) / 2

    # Розрахунок середніх рівнів Боллінджера для 30-хвилинного та 1-годинного таймфреймів
    bb_30m_range = data_30m['UpperBand'].iloc[-1] - data_30m['LowerBand'].iloc[-1]
    bb_1h_range = data_1h['UpperBand'].iloc[-1] - data_1h['LowerBand'].iloc[-1]
    average_bb_range = (bb_30m_range + bb_1h_range) / 2

    # Розрахунок стоплосу і тейкпрофіту
    stop_loss = entry_price - average_atr if side == SIDE_BUY else entry_price + average_atr
    take_profit = entry_price + average_bb_range if side == SIDE_BUY else entry_price - average_bb_range

    logger.info(f"Розрахований стоплос: {stop_loss}, тейкпрофіт: {take_profit}")

    return stop_loss, take_profit

# Функція для динамічного оновлення даних
def update_data(data_frames, new_data):
    for key in data_frames.keys():
        df = pd.DataFrame(new_data[key])
        data_frames[key] = pd.concat([data_frames[key], df]).drop_duplicates().reset_index(drop=True)
    return data_frames

# Основна функція для запуску торгової логіки
async def run_trading_logic():
    global client
    client = await AsyncClient.create(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'), testnet=True)

    open_order = None

    try:
        while True:
            data_frames = await fetch_latest_data()
            total_weight = 0

            # Розрахунок ваги для кожного таймфрейму та виведення в лог
            for timeframe, df in data_frames.items():
                logger.info(f"Аналіз даних для таймфрейму {timeframe}...")
                weight = calculate_indicator_weights(df)
                total_weight += weight
                logger.info(f"Вага для таймфрейму {timeframe}: {weight}%")

            logger.info(f"Загальна вага: {total_weight}%")

            # Прийняття рішення на основі загальної ваги
            if total_weight >= entry_threshold and open_order is None:
                # Відкриття лонг позиції
                entry_price = data_frames['1m']['Close'].iloc[-1]
                stop_loss, take_profit = calculate_stop_loss_take_profit(data_frames['30m'], data_frames['1h'], entry_price, SIDE_BUY)
                await open_position(client, symbol, SIDE_BUY, entry_price, stop_loss, take_profit)
                open_order = {'symbol': symbol, 'side': SIDE_BUY, 'entry_price': entry_price}
                logger.info("Позиція відкрита на основі позитивної загальної ваги.")

            elif total_weight <= -entry_threshold and open_order is None:
                # Відкриття шорт позиції
                entry_price = data_frames['1m']['Close'].iloc[-1]
                stop_loss, take_profit = calculate_stop_loss_take_profit(data_frames['30m'], data_frames['1h'], entry_price, SIDE_SELL)
                await open_position(client, symbol, SIDE_SELL, entry_price, stop_loss, take_profit)
                open_order = {'symbol': symbol, 'side': SIDE_SELL, 'entry_price': entry_price}
                logger.info("Позиція відкрита на основі негативної загальної ваги.")

            await asyncio.sleep(60)  # Очікування перед наступною перевіркою

    except BinanceAPIException as e:
        logger.error(f"Виникла помилка Binance API: {e}")
    except Exception as e:
        logger.error(f"Несподівана помилка: {e}")
    finally:
        if client:
            await client.close_connection()
            logger.info("З'єднання з Binance успішно закрито.")

# Функція для відкриття позиції з стоплосом і тейкпрофітом
async def open_position(client, symbol, side, entry_price, stop_loss, take_profit):
    try:
        # Встановлення кредитного плеча
        await client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"Кредитне плече встановлено на {leverage}x для {symbol}")

        # Визначення кількості на основі ризику та кредитного плеча
        balance_info = await client.futures_account_balance()
        balance = float([asset['balance'] for asset in balance_info if asset['asset'] == 'USDT'][0])
        risk_amount = balance / 5  # Ризикуємо 20% від балансу
        quantity = (risk_amount * leverage) / entry_price

        # Оформлення ринкового ордера
        order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=quantity
        )
        logger.info(f"Позиція {side} відкрита: {order}")

        # Встановлення стоп-лоса та тейк-профіту
        stop_order = await client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
            type='STOP_MARKET',
            stopPrice=stop_loss,
            closePosition=True
        )
        logger.info(f"Стоп-лос встановлено: {stop_order}")

        take_profit_order = await client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
            type='TAKE_PROFIT_MARKET',
            stopPrice=take_profit,
            closePosition=True
        )
        logger.info(f"Тейк-профіт встановлено: {take_profit_order}")

    except BinanceAPIException as e:
        logger.error(f"Помилка при відкритті позиції: {e}")
    except Exception as e:
        logger.error(f"Несподівана помилка при відкритті позиції: {e}")

import pandas_ta as ta  # Переконайтеся, що ця бібліотека встановлена

# Функція для отримання останніх даних з Binance
async def fetch_latest_data():
    try:
        data_frames = {}
        timeframes = ['1m', '5m', '15m', '30m', '1h']
        for timeframe in timeframes:
            klines = await client.get_klines(symbol=symbol, interval=timeframe, limit=500)
            df = pd.DataFrame(klines, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_asset_volume', 'Number_of_trades', 'Taker_buy_base', 'Taker_buy_quote', 'Ignore'])
            
            # Перетворення колонок на числовий тип
            numeric_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            for col in numeric_columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df.dropna(inplace=True)  # Видалення рядків з NaN після конвертації
            data_frames[timeframe] = df

        return data_frames
    except BinanceAPIException as e:
        logger.error(f"Помилка при отриманні даних з Binance: {e}")
    except Exception as e:
        logger.error(f"Несподівана помилка при отриманні даних: {e}")

# Основна частина програми для запуску
if __name__ == "__main__":
    try:
        asyncio.run(run_trading_logic())  # Передача реальних даних
    except KeyboardInterrupt:
        logger.info("Бот зупинений користувачем.")
        print("Бот зупинений користувачем.")
    except Exception as e:
        logger.error(f"Несподівана помилка: {e}")
        print(f"Несподівана помилка: {e}")
