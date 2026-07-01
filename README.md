# DNS-renewer

Обновляет **A** и **AAAA** записи в REG.RU для одного или нескольких доменов, подставляя **публичный IP текущей машины**. REG.RU — отвратительный регистратор, ни за что не пользуйтесь им!

Зависимости: Python 3.9+, `curl` не нужен — только stdlib и клиентский SSL-сертификат Reg.ru.

## Требования

1. DNS домена на NS Reg.ru (`ns1.reg.ru`, `ns2.reg.ru`).
2. В личном кабинете Reg.ru: API включён, **исходящий IP сервера в whitelist**, загружен клиентский SSL-сертификат.
3. На сервере:
   - `regru.crt` + `regru.key` (по умолчанию ищет в `~/Desktop/` или `/root/`)
   - файл с `REGU_USER` и `REGU_PASS`

## Установка

```bash
git clone <repo-url> DNS-renewer
cd DNS-renewer

cp config.json.example config.json
cp .env.example .env
chmod 600 .env
# отредактируйте config.json (zones) и .env (REGU_USER, REGU_PASS)

python3 dns_renewer.py --dry-run -v
python3 dns_renewer.py -v
```

### Учётные данные Reg.ru

Скрипт ищет первый файл, где заданы оба параметра:

1. `DNS-renewer/.env`
2. `~/.regru_api_env`
3. `/root/regru_api.env`

Путь можно задать явно: `--env-file /path/to/env`.

## Конфигурация

```json
{
  "ip_detection": {
    "ipv4": {
      "methods": ["local", "url"],
      "use_local_interface": true,
      "urls": ["https://api.ipify.org", "https://ifconfig.me/ip"]
    },
    "ipv6": {
      "enabled": false,
      "methods": ["local", "url"],
      "use_local_interface": true
    }
  },
  "regru": {
    "env_file": "~/.regru_api_env",
    "ssl": { "cert": "", "key": "" }
  },
  "zones": [
    {
      "domain": "example.com",
      "subdomains": ["@", "www"],
      "record_types": { "a": true, "aaaa": true }
    }
  ]
}
```

### IP текущей машины

| Метод | Описание |
|-------|----------|
| `local` | Исходящий интерфейс (`ip route get 1.1.1.1`). Прямой публичный IP на сервере. |
| `url` | Внешний сервис (ipify и т.п.). Нужен за NAT. |

Порядок — массив `methods`, по умолчанию `["local", "url"]`.

### Несколько доменов

Добавьте объекты в `zones`. CNAME и прочие записи **не изменяются** — перед первым запуском удалите старые CNAME в Reg.ru.

```bash
python3 dns_renewer.py --zone example.com --dry-run -v
```

### Несколько аккаунтов Reg.ru (перенос домена)

Если домены в **разных** аккаунтах Reg.ru, у зоны в `config.json` укажите свой `regru.env_file` и SSL:

```json
{
  "domain": "other.example.com",
  "subdomains": ["@", "www"],
  "record_types": { "a": true, "aaaa": false },
  "regru": {
    "env_file": ".env.other",
    "ssl": {
      "cert": "~/Desktop/regru-other.crt",
      "key": "~/Desktop/regru-other.key"
    }
  }
}
```

Зоны без блока `regru` используют общий `.env` и сертификаты по умолчанию (`~/Desktop/regru.crt` / `regru.key`).

```bash
cp .env.other.example .env.other && chmod 600 .env.other
# заполнить REGU_USER / REGU_PASS (или REGU_SSL_* в .env.other)
python3 dns_renewer.py --zone other.example.com --dry-run -v
```

В **новом** аккаунте: API включён, whitelist IP (CIDR Дом.ru), клиентский SSL загружен в кабинет и сохранён на сервере.

## systemd (автообновление каждые 10 мин)

```bash
sudo ./install-systemd.sh
```

Скрипт подставит путь к каталогу и пользователя в unit-файл, установит таймер и включит его.

```bash
journalctl -u dns-renewer.service -n 20
systemctl list-timers dns-renewer.timer
```

## Whitelist Reg.ru API (Дом.ru / динамический IP)

Если `dns_renewer.py` отвечает `ACCESS_DENIED_FROM_IP`, в [Настройках API Reg.ru](https://www.reg.ru/user/account/#/settings/api) в блоке **«Диапазоны IP-адресов»** нужно добавить **исходящий** публичный IP машины, с которой идут запросы (не IP из A-записи домена).

При **динамическом IP Дом.ru** (ЭР-Телеком) удобнее добавить CIDR-подсети провайдера, а не один адрес:

| Файл | Когда использовать |
|------|-------------------|
| `scripts/regru-whitelist-domru-as50543.txt` | **Рекомендуется** — региональная сеть (Саратов и др.), 19 префиксов. Ваш `79.136.193.217` ∈ `79.136.192.0/21` |
| `scripts/regru-whitelist-domru-all.txt` | AS50543 + магистраль AS9049, если IP «прыгает» между ASN |

Скопируйте строки (без `#`-комментариев) в Reg.ru → **Добавить IP** → **Сохранить**. Поддерживается CIDR (`79.136.192.0/21`).

Обновить списки из RIPE Stat:

```bash
python3 scripts/generate-regru-whitelist-domru.py --asn AS50543 > /tmp/domru.txt
python3 scripts/generate-regru-whitelist-domru.py   # AS50543 + AS9049
```

Проверка API после сохранения whitelist:

```bash
python3 dns_renewer.py --dry-run -v
```

**Безопасность:** whitelist по целому AS50543 широкий — держите **альтернативный пароль API** и клиентский SSL (`regru.crt`), не публикуйте `.env`.
