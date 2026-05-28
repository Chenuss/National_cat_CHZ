import requests
import json

# Конфигурация
BASE_URL = "https://апи.национальный-каталог.рф"
API_KEY = "xw84vx03u38jrtk5"  # Ваш ключ из лога
GOOD_ID = "70871481"  # Товар, который точно есть

def make_request(url, params=None, headers=None, method="GET"):
    print(f"\n--- Тест: {method} {url} ---")
    if params:
        print(f"Параметры: {params}")
    
    req_headers = headers or {}
    req_headers["apikey"] = API_KEY
    
    try:
        if method == "GET":
            resp = requests.get(url, params=params, headers=req_headers, timeout=10)
        elif method == "POST":
            resp = requests.post(url, json=params, headers=req_headers, timeout=10)
            
        print(f"Статус: {resp.status_code}")
        print(f"Тело ответа: {resp.text[:500] if resp.text else '(пусто)'}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                print(f"Успех! Данные получены.")
                return True
            except:
                print("Успех! Но ответ не JSON.")
                return True
        return False
    except Exception as e:
        print(f"Ошибка соединения: {e}")
        return False

def main():
    print(f"Диагностика доступа к товару {GOOD_ID}")
    print("="*50)

    # Вариант 1: Текущий (который выдает 404)
    make_request(
        f"{BASE_URL}/v3/feed-product",
        params={"good_ids": GOOD_ID, "subaccount": "true"}
    )

    # Вариант 2: Без subaccount
    make_request(
        f"{BASE_URL}/v3/feed-product",
        params={"good_ids": GOOD_ID}
    )

    # Вариант 3: Версия v2 (иногда методы меняются между версиями)
    make_request(
        f"{BASE_URL}/v2/feed-product",
        params={"good_ids": GOOD_ID, "subaccount": "true"}
    )

    # Вариант 4: Попытка получить через метод card (частый паттерн)
    make_request(
        f"{BASE_URL}/v3/card",
        params={"good_id": GOOD_ID} # Единичный ID часто называется good_id
    )

    # Вариант 5: POST запрос вместо GET (некоторые API требуют POST для фильтров)
    make_request(
        f"{BASE_URL}/v3/feed-product",
        params={"good_ids": GOOD_ID, "subaccount": "true"},
        method="POST"
    )
    
    # Вариант 6: Проверка базового пинга API (метод version или аналогичный)
    make_request(f"{BASE_URL}/v3/version")

if __name__ == "__main__":
    main()
