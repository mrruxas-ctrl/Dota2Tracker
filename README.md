# Dota 2 Steam ID Tracker

PyQt5-приложение для сбора Steam ID игроков из Dota 2 по экранным координатам, хранения ID в SQLite, заметок по игрокам, ignore-list, OCR-проверки ID и проверки Steam level профилей.

## Скачать exe

Готовый `Dota2Tracker.exe` лежит отдельно в GitHub Releases:

https://github.com/mrruxas-ctrl/Dota2Tracker/releases

Exe не хранится в исходниках, чтобы при скачивании кода не тянуть тяжелый бинарник.

Для запуска на другом ПК достаточно положить рядом:

```text
Dota2Tracker.exe
config.json   # опционально, если переносишь свои настройки
tracker.db    # опционально, если переносишь свою базу
```

Если `config.json` и `tracker.db` отсутствуют, приложение создаст их само.

## Запуск из исходников

```powershell
python -m pip install -r requirements.txt
python main.pyw
```

OCR для готового exe работает через встроенный portable Tesseract. В исходниках папки моделей и Tesseract не хранятся. Если запускаешь из Python, можно поставить Tesseract в систему или положить portable Tesseract в:

```text
ocr/tesseract/tesseract.exe
ocr/tesseract/tessdata/eng.traineddata
```

## Возможности

- сбор Steam ID из 10 слотов Dota 2 по настраиваемым координатам;
- горячие клавиши старта и остановки, назначаются нажатием клавиши в GUI;
- удержание клавиши списка игроков во время открытия слота;
- SQLite-база игроков с заметками, датами, счетчиком встреч и ignore-list;
- OCR-проверка ID по выделенной области экрана, 100% совпадение по цифрам;
- Windows-уведомление со звуком сразу при найденном ID из базы;
- открытие Steam-профиля по ID;
- проверка Steam level профиля с задержкой между запросами;
- красная обводка строк, если Steam level ниже заданного порога;
- темный PyQt5 UI.

## Файлы данных

- `config.json`: настройки координат, hotkey, OCR-области, задержек и Steam level.
- `tracker.db`: база игроков и ignore-list.
- `ocr/`: рабочая папка OCR при запуске из исходников.

## Проверка

```powershell
python -m py_compile main.pyw automation.py config.py db.py ocr_service.py steam_profile.py
python -m unittest discover -s tests -v
```
