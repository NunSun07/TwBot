import socket
import requests
import time
import json
import datetime
import os
import pytz
import logging
import threading
from typing import Dict, List, Optional

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TwitchFACEITBot:
    """
    Twitch бот для відстеження FACEIT статистики
    """
    
    def __init__(self):
        # Cooldown для команди !elo
        self.elo_cooldown = 5   # секунди
        self.last_elo_time = 0
        self.pending_elo_thread = None

        # Змінні середовища
        self.SERVER = "irc.twitch.tv"
        self.PORT = 6667  # SSL порт
        self.TOKEN = os.environ.get("TWITCH_OAUTH_TOKEN")
        self.NICK = os.environ.get("TWITCH_BOT_NICK")
        self.CHANNEL = os.environ.get("TWITCH_CHANNEL")
        self.FACEIT_API_KEY = os.environ.get("FACEIT_API_KEY")
        self.FACEIT_NICK = os.environ.get("FACEIT_NICK")

        self.TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID")        # з ENV
        self.TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")  # з ENV
        self.TWITCH_APP_TOKEN = None
        self.TOKEN_EXPIRES_AT = 0  # час, коли токен закінчується (timestamp)

        self.ELO_FILE = os.getenv("ELO_FILE_PATH", "elo_history.json")
        self.TIMEZONE = pytz.timezone('Europe/Kiev')

        # Перевірка необхідних ENV
        required_env = [
            'TWITCH_OAUTH_TOKEN', 'TWITCH_BOT_NICK', 'TWITCH_CHANNEL',
            'FACEIT_API_KEY', 'FACEIT_NICK', 'TWITCH_CLIENT_ID', 'TWITCH_APP_TOKEN'
        ]
        for var in required_env:
            if not os.environ.get(var):
                logging.warning(f"⚠️ ENV змінна {var} не задана!")

        # IRC підключення
        self.irc = socket.socket()
        self.irc.settimeout(30)
        self.running = False

        # Ініціалізація файлу Elo
        if not os.path.exists(self.ELO_FILE):
            initial_data = [{
                "elo": 0,
                "timestamp": datetime.datetime.now(self.TIMEZONE).isoformat()
            }]
            with open(self.ELO_FILE, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f, indent=2, ensure_ascii=False)
            logging.info(f"Файл {self.ELO_FILE} створено з початковим значенням Elo = 0")

        logging.info("Ініціалізація бота завершена. Elo файл готовий.")

        # Планування щоденного обнулення о 4 ранку
        self.schedule_daily_reset()
        logging.info("Щоденне обнулення Elo заплановане на 04:00")

    def refresh_twitch_token(self):
        """Отримує новий токен Twitch через Client Credentials"""
        import time
        try:
            url = (
                f"https://id.twitch.tv/oauth2/token"
                f"?client_id={self.TWITCH_CLIENT_ID}"
                f"&client_secret={self.TWITCH_CLIENT_SECRET}"
                f"&grant_type=client_credentials"
            )
            response = requests.post(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            self.TWITCH_APP_TOKEN = data['access_token']
            self.TOKEN_EXPIRES_AT = time.time() + data['expires_in'] - 60  # мінус 60 сек запас
            logging.info(f"🔑 Отримано новий токен Twitch, expires_in={data['expires_in']} сек")

        except Exception as e:
            logging.error(f"❌ Не вдалося отримати токен Twitch: {e}")

    def ensure_twitch_token(self):
        """Перевіряє, чи токен ще дійсний, і оновлює його, якщо потрібно"""
        import time
        if not self.TWITCH_APP_TOKEN or time.time() >= self.TOKEN_EXPIRES_AT:
            logging.info("🔄 Токен Twitch закінчився або не існує, оновлюємо...")
            self.refresh_twitch_token()

    def reset_daily_stats(self):
        """Обнуляє денну статистику (Win/Lose/зміни Elo)"""
        if not os.path.exists(self.ELO_FILE):
            return

        try:
            with open(self.ELO_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)

            last_elo = history[-1]['elo'] if history else 0
            new_entry = {
                "elo": last_elo,
                "timestamp": datetime.datetime.now(self.TIMEZONE).isoformat()
            }

            with open(self.ELO_FILE, 'w', encoding='utf-8') as f:
                json.dump([new_entry], f, indent=2, ensure_ascii=False)

            logging.info("🔄 Денна статистика обнулена о 4 ранку")
        except Exception as e:
            logging.error(f"Помилка при обнуленні статистики: {e}")

    def schedule_daily_reset(self):
        """Запускає таймер, який викликає reset_daily_stats щодня о 4 ранку"""
        now = datetime.datetime.now(self.TIMEZONE)
        next_reset = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= next_reset:
            next_reset += datetime.timedelta(days=1)

        delay = (next_reset - now).total_seconds()
        import threading
        threading.Timer(delay, self._daily_reset_callback).start()

    def _daily_reset_callback(self):
        """Викликається таймером, обнуляє статистику і планує наступне обнулення"""
        self.reset_daily_stats()
        self.schedule_daily_reset()  # плануємо наступне обнулення

    def connect_to_twitch(self) -> bool:
        """Підключення до Twitch IRC"""
        try:
            self.irc = socket.socket()
            self.irc.settimeout(30)  # Таймаут для підключення
            self.irc.connect((self.SERVER, self.PORT))
            
            # Автентифікація
            self.irc.send(f"PASS {self.TOKEN}\r\n".encode('utf-8'))
            self.irc.send(f"NICK {self.NICK}\r\n".encode('utf-8'))
            self.irc.send(f"JOIN #{self.CHANNEL}\r\n".encode('utf-8'))
            
            logger.info(f"✅ Бот {self.NICK} підключився до каналу {self.CHANNEL}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Помилка підключення до Twitch: {e}")
            return False
    
    def is_stream_live(self) -> bool:
        return True

    def send_message(self, message: str):
        """Надіслати повідомлення в чат з контролем частоти та підтримкою Unicode"""
        try:
            if not self.irc:
                logger.warning("IRC не підключено")
                return
            
            # Twitch обмежує швидкість ~20 повідомлень на 30 сек
            # Робимо невелику паузу між повідомленнями
            time.sleep(0.5)

            # Надсилаємо повідомлення у чат
            self.irc.send(f"PRIVMSG #{self.CHANNEL} :{message}\r\n".encode('utf-8'))

            logger.info(f"Надіслано: {message}")

        except Exception as e:
            logger.error(f"Помилка надсилання повідомлення: {e}")
    
    def clean_old_elo_records(self):
        """Очищення старих записів (залишаємо тільки поточний день)"""
        if not os.path.exists(self.ELO_FILE):
            return
            
        try:
            with open(self.ELO_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            
            today = datetime.datetime.now(self.TIMEZONE).date()
            history = [
                entry for entry in history 
                if datetime.datetime.fromisoformat(entry['timestamp']).date() >= today
            ]
            
            with open(self.ELO_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
                
            logger.info(f"Очищено старі записи в {self.ELO_FILE}")
            
        except Exception as e:
            logger.error(f"Помилка при очищенні Elo записів: {e}")
    
    def save_elo_record(self, elo: int):
        """Збереження запису Elo з часовою міткою"""
        timestamp = datetime.datetime.now(self.TIMEZONE).isoformat()
        data = {"elo": elo, "timestamp": timestamp}
        
        try:
            # Читаємо існуючу історію
            if os.path.exists(self.ELO_FILE):
                with open(self.ELO_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            else:
                history = []
            
            history.append(data)
            
            # Зберігаємо оновлену історію
            with open(self.ELO_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
                
            logger.info(f"Збережено Elo: {elo} на час {timestamp}")
            
        except Exception as e:
            logger.error(f"Помилка при збереженні Elo: {e}")
    
    def get_daily_elo_change(self) -> int:
        """Отримання зміни Elo за поточний день без врахування стартових нулів"""
        if not os.path.exists(self.ELO_FILE):
            logger.info("Файл історії не існує, денна зміна = 0")
            return 0
    
        try:
            with open(self.ELO_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
    
            if not history:
                logger.info("Історія порожня, денна зміна = 0")
                return 0
    
            today = datetime.datetime.now(self.TIMEZONE).date()
            daily_records = [
                entry for entry in history 
                if datetime.datetime.fromisoformat(entry['timestamp']).date() == today
            ]
    
            # Ігноруємо перший запис із початковим 0 Elo
            while daily_records and daily_records[0]['elo'] == 0:
                daily_records.pop(0)
    
            if len(daily_records) < 2:
                logger.info("Недостатньо записів за сьогодні для розрахунку зміни")
                return 0
    
            first_elo = daily_records[0]['elo']
            latest_elo = daily_records[-1]['elo']
            change = latest_elo - first_elo
    
            logger.info(f"Денна зміна Elo: {latest_elo} - {first_elo} = {change}")
            return change

        except Exception as e:
            logger.error(f"Помилка при читанні історії Elo: {e}")
            return 0
    
    def get_faceit_stats(self, nickname: str) -> Dict[str, int]:
        """Отримання статистики з FACEIT API"""
        headers = {'Authorization': f'Bearer {self.FACEIT_API_KEY}'}
        
        try:
            # Отримуємо базову інформацію про гравця
            player_url = f"https://open.faceit.com/data/v4/players?nickname={nickname}"
            response = requests.get(player_url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"Помилка API players: статус {response.status_code}")
                return self._get_empty_stats()
            
            player_data = response.json()
            player_id = player_data.get('player_id')
            
            if not player_id:
                logger.error("Не знайдено player_id")
                return self._get_empty_stats()
            
            # Отримуємо Elo
            cs2_stats = player_data.get('games', {}).get('cs2', {})
            elo = cs2_stats.get('faceit_elo', 0)
            
            # Отримуємо матчі за сьогодні
            wins, losses = self._get_daily_matches(player_id, headers)
            
            stats = {'Elo': elo, 'Win': wins, 'Lose': losses}
            logger.info(f"Отримано статистику: {stats}")
            return stats
            
        except requests.RequestException as e:
            logger.error(f"Помилка запиту до FACEIT API: {e}")
            return self._get_empty_stats()
        except Exception as e:
            logger.error(f"Неочікувана помилка при отриманні статистики: {e}")
            return self._get_empty_stats()
    
    def _get_daily_matches(self, player_id: str, headers: Dict[str, str]) -> tuple[int, int]:
        """Отримання матчів за поточний день (корекція з UTC)"""
        try:
            # Використовуємо UTC для API
            today_utc = datetime.datetime.utcnow().date()
            from_time = int(datetime.datetime.combine(today_utc, datetime.time(0, 0)).timestamp())
            to_time = int(datetime.datetime.utcnow().timestamp())
            
            logger.info(f"🔍 Пошук матчів з {datetime.datetime.utcfromtimestamp(from_time)} до {datetime.datetime.utcfromtimestamp(to_time)} (UTC)")
            
            matches_url = f"https://open.faceit.com/data/v4/players/{player_id}/history"
            params = {'game': 'cs2', 'from': from_time, 'to': to_time, 'limit': 100}  # збільшений ліміт
            
            response = requests.get(matches_url, headers=headers, params=params, timeout=15)
            if response.status_code != 200:
                logger.error(f"❌ Помилка API matches: статус {response.status_code}, відповідь: {response.text}")
                return 0, 0
            
            matches_data = response.json()
            matches = matches_data.get('items', [])
            logger.info(f"📈 Знайдено матчів: {len(matches)}")
            
            wins = 0
            losses = 0
            
            for i, match in enumerate(matches):
                logger.info(f"🎮 Обробка матчу {i+1}/{len(matches)} - {match.get('match_id')}")
                result = self._analyze_match(match, player_id)
                if result == "win":
                    wins += 1
                elif result == "loss":
                    losses += 1
            
            logger.info(f"📊 Фінальний результат за день: Wins={wins}, Losses={losses}")
            return wins, losses
        
        except Exception as e:
            logger.error(f"Помилка при отриманні матчів: {e}")
            return 0, 0

    def _analyze_match(self, match: Dict, player_id: str) -> str:
        try:
            if match.get("status") != "finished":
                return "unknown"

            teams = match.get("teams", {})
            results = match.get("results", {})

            # 1. Знайти команду гравця
            player_team = None
            for faction, team_data in teams.items():
                for p in team_data.get("players", []):
                    if p.get("player_id") == player_id:
                        player_team = faction
                        break
                if player_team:
                    break

            if not player_team:
                return "unknown"

            # 2. Перевірити переможця
            winner = results.get("winner")
            if not winner:
                return "unknown"

            return "win" if player_team == winner else "loss"

        except Exception as e:
            logger.error(f"Помилка при аналізі матчу: {e}")
            return "unknown"


    def _get_recent_matches_fallback(self, player_id: str, headers: Dict[str, str]) -> tuple[int, int]:
        """Запасний метод: отримання матчів за останні 3 дні"""
        try:
            logger.info("🔄 Використовуємо запасний метод для отримання матчів")
            
            # Останні 3 дні
            end_date = datetime.datetime.now(self.TIMEZONE)
            start_date = end_date - datetime.timedelta(days=3)
            
            from_time = int(start_date.timestamp())
            to_time = int(end_date.timestamp())
            
            matches_url = f"https://open.faceit.com/data/v4/players/{player_id}/history"
            params = {
                'game': 'cs2',
                'from': from_time,
                'to': to_time,
                'limit': 50
            }
            
            response = requests.get(matches_url, headers=headers, params=params, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"Запасний метод також не працює: {response.status_code}")
                return 0, 0
            
            matches_data = response.json()
            matches = matches_data.get('items', [])
            
            logger.info(f"📈 Знайдено матчів за останні 3 дні: {len(matches)}")
            
            if matches:
                logger.info("Приклад структури матчу:")
                logger.info(json.dumps(matches[0], indent=2))
            
            # Повертаємо 0, оскільки це тільки для діагностики
            return 0, 0
            
        except Exception as e:
            logger.error(f"Помилка в запасному методі: {e}")
            return 0, 0
    
    def _get_empty_stats(self) -> Dict[str, int]:
        """Повертає порожню статистику у випадку помилки"""
        return {'Elo': 0, 'Win': 0, 'Lose': 0}
    
    def handle_command(self, username: str, message: str):
        """Обробка команд від користувачів"""
        message = message.lower().strip()
        
        if message == '!elo':
            self._handle_elo_command(username)
        elif message == '!checkelo':
            self._handle_checkelo_command(username)
        elif message == '!debug':
            self._handle_debug_command(username)
        elif message == '!testapi':
            self._handle_testapi_command(username)
    
    def _handle_elo_command(self, username: str):
        """Обробка команди !elo з фоновою обробкою та cooldown"""
        import threading
        import time

        current_time = time.time()

        # Якщо cooldown ще не пройшов
        if current_time - self.last_elo_time < self.elo_cooldown:
            logger.info(f"Команда !elo від {username} заблокована (cooldown)")
            return

        # Якщо вже є активний фоновий потік
        if hasattr(self, 'pending_elo_thread') and self.pending_elo_thread and self.pending_elo_thread.is_alive():
            logger.info(f"Команда !elo від {username} заблокована (фонова обробка триває)")
            return

        # Запускаємо фонову обробку
        self.pending_elo_thread = threading.Thread(target=self._process_elo, args=(username,))
        self.pending_elo_thread.start()

    def _process_elo(self, username: str):
        """Фонова обробка статистики та відправка повідомлення"""
        import time

        try:
            stats = self.get_faceit_stats(self.FACEIT_NICK)
            daily_change = self.get_daily_elo_change()

            if stats['Elo'] > 0:
                self.save_elo_record(stats['Elo'])

            change_str = f"+{daily_change}" if daily_change > 0 else str(daily_change)

            response = (
                f"@{username} → Elo: {stats['Elo']} | "
                f"Win: {stats['Win']} | "
                f"Lose: {stats['Lose']} | "
                f"{change_str}"
            )

            # Відправка повідомлення
            logger.info(f"Відправляю статистику для {username}")
            self.send_message(response)

            # Оновлюємо час останнього запиту після завершення
            self.last_elo_time = time.time()

        except Exception as e:
            logger.error(f"Помилка під час обробки !elo: {e}")


    def _process_elo(self, username: str):
        """Фонова обробка та відправка фінальної статистики"""
        stats = self.get_faceit_stats(self.FACEIT_NICK)
        daily_change = self.get_daily_elo_change()

        if stats['Elo'] > 0:
            self.save_elo_record(stats['Elo'])

        change_str = f"+{daily_change}" if daily_change > 0 else str(daily_change)

        response = (
            f"@{username} → Elo: {stats['Elo']} | "
            f"Win: {stats['Win']} | "
            f"Lose: {stats['Lose']} | "
            f"{change_str}"
        )

        self.send_message(response)

        # Встановлюємо останній час після завершення обробки
        self.last_elo_time = time.time()


    def _handle_checkelo_command(self, username: str):
        """Обробка команди !checkelo (виводить в консоль)"""
        logger.info(f"Обробка команди !checkelo від {username}")
        
        stats = self.get_faceit_stats(self.FACEIT_NICK)
        daily_change = self.get_daily_elo_change()
        
        print(f"\n=== Перевірка для @{username} ===")
        print(f"Поточне Elo: {stats['Elo']}")
        print(f"Win (сьогодні): {stats['Win']}")
        print(f"Lose (сьогодні): {stats['Lose']}")
        print(f"Денна зміна Elo: {'+' if daily_change >= 0 else ''}{daily_change}")
        print("=" * 35)
        
        # Додаткова діагностика, якщо Win/Lose = 0
        if stats['Win'] == 0 and stats['Lose'] == 0:
            print("\n⚠️  ДІАГНОСТИКА: Win та Lose = 0")
            print("Можливі причини:")
            print("1. Сьогодні не було зіграно жодного матчу")
            print("2. Матчі ще не обробились API FACEIT")
            print("3. Неправильний nickname або player_id")
            print("4. Проблема з часовим поясом")
            print("5. API повертає некоректні дані")
            print("\nПеревірте логи вище для детальної інформації")
            print("=" * 50)
    
    def _handle_test_command(self, username: str):
        """Обробка команди !test"""
        self.send_message(f"@{username} Бот працює! ✅")
    
    def _handle_debug_command(self, username: str):
        """Обробка команди !debug - детальна діагностика"""
        logger.info(f"Обробка команди !debug від {username}")
        
        headers = {'Authorization': f'Bearer {self.FACEIT_API_KEY}'}
        
        try:
            # Тест 1: Перевірка player endpoint
            player_url = f"https://open.faceit.com/data/v4/players?nickname={self.FACEIT_NICK}"
            response = requests.get(player_url, headers=headers, timeout=10)
            
            print(f"\n=== DEBUG для @{username} ===")
            print(f"1. Player API статус: {response.status_code}")
            
            if response.status_code == 200:
                player_data = response.json()
                player_id = player_data.get('player_id')
                print(f"   Player ID: {player_id}")
                print(f"   Nickname: {player_data.get('nickname')}")
                
                cs2_stats = player_data.get('games', {}).get('cs2', {})
                print(f"   CS2 Elo: {cs2_stats.get('faceit_elo', 'N/A')}")
                
                # Тест 2: Перевірка matches endpoint
                if player_id:
                    today = datetime.datetime.now(self.TIMEZONE)
                    yesterday = today - datetime.timedelta(days=1)
                    
                    from_time = int(yesterday.timestamp())
                    to_time = int(today.timestamp())
                    
                    matches_url = f"https://open.faceit.com/data/v4/players/{player_id}/history"
                    params = {'game': 'cs2', 'from': from_time, 'to': to_time, 'limit': 10}
                    
                    matches_response = requests.get(matches_url, headers=headers, params=params, timeout=10)
                    print(f"2. Matches API статус: {matches_response.status_code}")
                    
                    if matches_response.status_code == 200:
                        matches_data = matches_response.json()
                        matches = matches_data.get('items', [])
                        print(f"   Знайдено матчів за останню добу: {len(matches)}")
                        
                        if matches:
                            print("   Останній матч:")
                            last_match = matches[0]
                            print(f"     Match ID: {last_match.get('match_id')}")
                            print(f"     Статус: {last_match.get('status')}")
                            print(f"     Дата: {last_match.get('started_at')}")
                    else:
                        print(f"   Помилка matches API: {matches_response.text}")
            else:
                print(f"   Помилка player API: {response.text}")
            
            print("=" * 40)
            
        except Exception as e:
            print(f"Debug помилка: {e}")
    
    def _handle_testapi_command(self, username: str):
        """Тест API з різними параметрами"""
        logger.info(f"Тестування API для {username}")
        
        headers = {'Authorization': f'Bearer {self.FACEIT_API_KEY}'}
        
        try:
            # Отримуємо player_id
            player_url = f"https://open.faceit.com/data/v4/players?nickname={self.FACEIT_NICK}"
            response = requests.get(player_url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                self.send_message(f"@{username} API помилка: {response.status_code}")
                return
            
            player_data = response.json()
            player_id = player_data.get('player_id')
            
            if not player_id:
                self.send_message(f"@{username} Player ID не знайдено")
                return
            
            # Тестуємо різні періоди
            now = datetime.datetime.now(self.TIMEZONE)
            periods = [
                ("Сьогодні", 0),
                ("Вчора", 1),
                ("2 дні тому", 2),
                ("Тиждень", 7)
            ]
            
            for period_name, days_ago in periods:
                start_date = now - datetime.timedelta(days=days_ago+1)
                end_date = now - datetime.timedelta(days=days_ago) if days_ago > 0 else now
                
                from_time = int(start_date.timestamp())
                to_time = int(end_date.timestamp())
                
                matches_url = f"https://open.faceit.com/data/v4/players/{player_id}/history"
                params = {'game': 'cs2', 'from': from_time, 'to': to_time, 'limit': 20}
                
                matches_response = requests.get(matches_url, headers=headers, params=params, timeout=10)
                
                if matches_response.status_code == 200:
                    matches_data = matches_response.json()
                    matches_count = len(matches_data.get('items', []))
                    print(f"{period_name}: {matches_count} матчів")
                else:
                    print(f"{period_name}: API помилка {matches_response.status_code}")
            
            self.send_message(f"@{username} Тест API завершено, перевірте консоль")
            
        except Exception as e:
            self.send_message(f"@{username} Помилка тесту API: {e}")
            logger.error(f"API test помилка: {e}")
    
    def run(self):
        """Основний цикл роботи бота з перевіркою стріму"""
        self.running = True
        logger.info("🚀 Бот запущено! Очікування команд...")

        while self.running:
            try:
                if not self.is_stream_live():
                    logger.info("Стрім не активний. Бот чекає...")
                    time.sleep(60)  # Перевіряти кожну хвилину
                    continue

                if not self.connect_to_twitch():
                    logger.error("Не вдалося підключитися до Twitch")
                    time.sleep(10)
                    continue

                self.clean_old_elo_records()

                while self.running and self.is_stream_live():
                    try:
                        response = self.irc.recv(2048).decode('utf-8', errors='ignore')
                        if not response:
                            logger.warning("Порожня відповідь від сервера, перепідключення...")
                            self._reconnect()
                            continue

                        if response.startswith('PING'):
                            self.irc.send("PONG\r\n".encode('utf-8'))
                            continue

                        if "PRIVMSG" in response:
                            self._parse_message(response)

                    except (socket.timeout, ConnectionResetError, ConnectionAbortedError) as e:
                        logger.warning(f"Розрив з'єднання: {e}, перепідключення...")
                        self._reconnect()
                        time.sleep(5)
                    except requests.RequestException as e:
                        logger.error(f"Помилка API: {e}, продовжуємо роботу...")
                        time.sleep(5)
                    except Exception as e:
                        logger.error(f"Неочікувана помилка: {e}")
                        time.sleep(1)

                logger.info("Стрім завершено або бот зупинено. Очікування наступного стріму...")
                if self.irc:
                    self.irc.close()
                time.sleep(60)

            except KeyboardInterrupt:
                logger.info("Отримано сигнал зупинки...")
                self.stop()
            except Exception as e:
                logger.error(f"Критична помилка в циклі run: {e}")
                time.sleep(5)

    
    def _parse_message(self, response: str):
        """Парсинг повідомлення з чату"""
        try:
            # Витягуємо ім'я користувача та повідомлення
            username = response.split('!', 1)[0][1:]
            message = response.split('PRIVMSG', 1)[1].split(':', 1)[1].strip()
            
            logger.info(f"{username}: {message}")
            self.handle_command(username, message)
            
        except Exception as e:
            logger.error(f"Помилка при парсингу повідомлення: {e}")
    
    def _reconnect(self):
        """Перепідключення до Twitch"""
        try:
            if self.irc:
                self.irc.close()
            time.sleep(5)
            self.connect_to_twitch()
        except Exception as e:
            logger.error(f"Помилка при перепідключенні: {e}")
    
    def stop(self):
        """Зупинка бота"""
        self.running = False
        if self.irc:
            self.irc.close()
        logger.info("Бот зупинено")

def main():
    """Головна функція"""
    bot = TwitchFACEITBot()
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Отримано сигнал зупинки...")
        bot.stop()
    except Exception as e:
        logger.error(f"Критична помилка: {e}")
        bot.stop()

if __name__ == "__main__":

    main()



