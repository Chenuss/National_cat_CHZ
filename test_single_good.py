import requests
import json

# Конфигурация
API_URL = "https://апи.национальный-каталог.рф"
API_KEY = "test_api_key_for_demo"  # Замените на ваш реальный ключ

# Тестовый good_id из лога
GOOD_ID = 70871481

# Формируем запрос
url = f"{API_URL}/v3/feed-product"
params = {
    "good_ids": str(GOOD_ID),
    "subaccount": "true",
    "apikey": API_KEY
}

print(f"Запрос к: {url}")
print(f"Параметры: {params}")

response = requests.get(url, params=params, timeout=30)

print(f"\nСтатус код: {response.status_code}")
print(f"Заголовки ответа:")
for key, value in response.headers.items():
    print(f"  {key}: {value}")

print(f"\nТело ответа:")
try:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
except:
    print(response.text[:2000])
