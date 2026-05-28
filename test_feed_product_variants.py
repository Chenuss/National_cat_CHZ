"""
Скрипт для тестирования различных вариантов запроса к API feed-product.
Проверяет разные методы аутентификации и форматы параметров.
"""

import requests
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

API_URL = "https://апи.национальный-каталог.рф"
API_KEY = "xw84vx03u38jrtk5"
GOOD_ID = 70871481

def test_variants():
    good_id = GOOD_ID
    
    variants = [
        {
            "name": "GET /v3/feed-product с good_ids (через запятую)",
            "method": "GET",
            "url": f"{API_URL}/v3/feed-product",
            "params": {"good_ids": str(good_id), "subaccount": "true"},
        },
        {
            "name": "GET /v3/feed-product с good_ids (массив через &)",
            "method": "GET",
            "url": f"{API_URL}/v3/feed-product",
            "params": {"good_ids[]": str(good_id), "subaccount": "true"},
        },
        {
            "name": "POST /v3/feed-product с JSON body",
            "method": "POST",
            "url": f"{API_URL}/v3/feed-product",
            "json": {"good_ids": [good_id], "subaccount": True},
        },
        {
            "name": "GET /v4/feed-product с good_ids",
            "method": "GET",
            "url": f"{API_URL}/v4/feed-product",
            "params": {"good_ids": str(good_id), "subaccount": "true"},
        },
        {
            "name": "GET /v3/product с good_id (единичный)",
            "method": "GET",
            "url": f"{API_URL}/v3/product",
            "params": {"good_id": good_id, "subaccount": "true"},
        },
    ]
    
    for variant in variants:
        print(f"\n{'='*60}")
        print(f"Тест: {variant['name']}")
        print(f"URL: {variant['url']}")
        
        try:
            if variant['method'] == 'GET':
                response = requests.get(
                    variant['url'],
                    params=variant.get('params'),
                    headers={"apikey": API_KEY} if False else {},
                )
                # Добавляем apikey в query параметры
                if 'params' not in variant or variant['params'] is None:
                    variant['params'] = {}
                variant['params']['apikey'] = API_KEY
                response = requests.get(variant['url'], params=variant['params'])
            elif variant['method'] == 'POST':
                response = requests.post(
                    variant['url'],
                    params={"apikey": API_KEY},
                    json=variant.get('json'),
                )
            
            print(f"Статус: {response.status_code}")
            print(f"Заголовки ответа:")
            for key, value in response.headers.items():
                if 'usage' in key.lower() or 'etag' in key.lower():
                    print(f"  {key}: {value}")
            
            if response.status_code == 200:
                print(f"УСПЕХ! Данные получены.")
                print(f"Тело ответа (первые 500 символов): {str(response.text)[:500]}")
                return
            else:
                print(f"Тело ответа: {response.text[:200] if response.text else '(пусто)'}")
                
        except Exception as e:
            print(f"Ошибка: {e}")

if __name__ == "__main__":
    print(f"Диагностика API feed-product для товара {GOOD_ID}")
    print(f"API Key: {API_KEY}")
    test_variants()
