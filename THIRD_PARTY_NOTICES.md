# Библиотеки и модели Ушастика

Лицензия [MIT](LICENSE) распространяется на собственный код Ушастика. Включённые библиотеки, нативные компоненты и веса моделей сохраняют лицензии и авторство своих разработчиков.

Полные тексты собраны в [`licenses/`](licenses) исходного репозитория и в папке **`LICENSES` в корне распакованного релиза** для Windows и macOS. Внутри дистрибутивов также сохранены лицензии моделей и сведения о зависимостях:

| Платформа | Лицензии моделей | Версии зависимостей |
| --- | --- | --- |
| Windows | `_internal/model/`, `_internal/speaker-model/` | `dependencies.json` рядом с `Ushastik.exe` |
| macOS | `Ушастик.app/Contents/Resources/model/`, `Ушастик.app/Contents/Resources/speaker-model/` | `Ушастик.app/Contents/Resources/dependencies.json` |

## Основные компоненты

| Компонент | Назначение | Лицензия / источник |
| --- | --- | --- |
| OpenAI Whisper Small | Модель распознавания речи | [MIT, © 2022 OpenAI](macos/model-notices/model/WHISPER-LICENSE.txt) |
| Systran faster-whisper | Распознавание речи | [MIT](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| CTranslate2 | Выполнение Whisper на CPU | [MIT](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) |
| sherpa-onnx | Разделение голосов и выполнение ONNX-моделей | [Apache-2.0](macos/model-notices/speaker-model/RUNTIME-LICENSE.txt) |
| pyannote segmentation 3.0, ONNX | Определение участков речи | [MIT, © 2022 CNRS](macos/model-notices/speaker-model/SEGMENTATION-LICENSE.txt) |
| 3D-Speaker ERes2NetV2, ONNX | Представление голоса для сравнения участников | [Apache-2.0](macos/model-notices/speaker-model/EMBEDDING-LICENSE.txt) |
| ONNX Runtime | Выполнение моделей | [MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| PyAV | Чтение аудио и видео через FFmpeg | [BSD-3-Clause](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) |
| FFmpeg и включённые кодеки | Декодирование медиа | [Условия конкретной сборки LGPL/GPL](https://ffmpeg.org/legal.html); тексты входят в `LICENSES` |
| NumPy | Обработка аудиомассивов | [BSD-3-Clause и условия включённых компонентов](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| Python | Среда выполнения | [PSF License и включённые уведомления](https://docs.python.org/3/license.html) |
| Tcl/Tk | Графический интерфейс | [Лицензии Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html) |
| tkinterdnd2 / tkdnd | Перетаскивание файлов | [MIT и BSD-style условия tkdnd](https://github.com/Eliav2/tkinterdnd2/blob/master/LICENSE) |
| PyInstaller | Упаковка приложения | [GPL-2.0-or-later с исключением для распространения собранных приложений](https://pyinstaller.org/en/stable/license.html) |

Это перечень основных компонентов, а не замена их полным лицензиям. Точные версии зависят от платформы и записаны в `dependencies.json`. Сопутствующие зависимости и их уведомления входят в `LICENSES`. Наличие компонента в сборке не означает, что приложение использует все его возможности: например, включённые клиентские библиотеки не служат для скачивания моделей во время работы Ушастика.

Нативный медиа-комплект PyAV включает FFmpeg, x264 и x265. Для x264/x265 действуют условия GPL; обозначение LGPL у FFmpeg не заменяет лицензии этих компонентов. Их исходники, исходники других включённых медиа-библиотек и сценарии сборки с патчами опубликованы в архиве **[ThirdParty-Sources-0.3.1.zip](https://github.com/kurzemnek/ushastik/releases/download/v0.3.1/ThirdParty-Sources-0.3.1.zip)** рядом с дистрибутивами. В архиве приведены версии, источники и контрольные суммы. Собственный код Ушастика и эти сторонние компоненты имеют отдельные лицензии.

## Происхождение моделей

Модели и SHA-256 зафиксированы в [`macos/models.json`](macos/models.json); [`fetch_models.py`](fetch_models.py) проверяет их до сборки.

- Whisper Small: [конвертированная модель Systran](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120), ревизия `536b0662742c02347bc0e980a01041f333bce120`.
- Segmentation: [публичная ONNX-модель pyannote 3.0 из sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models).
- Embedding: [публичная ONNX-модель 3D-Speaker ERes2NetV2 из sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models).

Имена проектов и их авторов приведены для атрибуции. Они не обозначают одобрение Ушастика этими авторами.
