from __future__ import annotations

import importlib.util
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
import pyautogui
from PIL import Image, ImageOps

from db import extract_digits, normalize_id


class OcrDependencyError(RuntimeError):
    pass


TESSERACT_INSTALL_URL = "https://github.com/UB-Mannheim/tesseract/wiki"

_DLL_DIR_HANDLES: list[Any] = []
_LAST_RUNTIME_PATHS: list[Path] = []


def prepare_ocr_runtime(project_ocr_dir: str | Path) -> list[Path]:
    project_ocr_dir = Path(project_ocr_dir).resolve()
    project_dir = project_ocr_dir.parent
    project_ocr_dir.mkdir(parents=True, exist_ok=True)
    (project_ocr_dir / "user_network").mkdir(parents=True, exist_ok=True)

    os.environ["EASYOCR_MODULE_PATH"] = str(project_ocr_dir)
    os.environ.setdefault("TORCH_HOME", str(project_ocr_dir / "torch_cache"))

    for package_dir in _external_package_dirs(project_ocr_dir):
        if package_dir.exists():
            path_text = str(package_dir)
            if path_text not in sys.path:
                sys.path.insert(0, path_text)

    dll_dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        mei_path = getattr(sys, "_MEIPASS", "")
        if mei_path:
            dll_dirs.extend(
                [
                    Path(mei_path),
                    Path(mei_path) / "torch" / "lib",
                    Path(mei_path) / "cv2" / "python-3.11",
                ]
            )

    dll_dirs.extend(
        [
            project_ocr_dir,
            project_dir / "site-packages" / "torch" / "lib",
            project_dir / "python_packages" / "torch" / "lib",
            project_dir / "python" / "torch" / "lib",
            project_ocr_dir / "torch" / "lib",
            project_ocr_dir / "site-packages" / "torch" / "lib",
            project_ocr_dir / "python_packages" / "torch" / "lib",
            project_ocr_dir / "python" / "torch" / "lib",
            project_ocr_dir / "tesseract",
            project_ocr_dir / "Tesseract-OCR",
            project_ocr_dir / "dll",
            project_ocr_dir / "bin",
        ]
    )

    torch_spec = importlib.util.find_spec("torch")
    if torch_spec and torch_spec.submodule_search_locations:
        torch_dir = Path(next(iter(torch_spec.submodule_search_locations)))
        dll_dirs.append(torch_dir / "lib")

    for package_root in _site_package_roots():
        dll_dirs.extend(
            [
                package_root / "torch" / "lib",
                package_root / "nvidia" / "cublas" / "bin",
                package_root / "nvidia" / "cuda_runtime" / "bin",
            ]
        )

    added: list[Path] = []
    for dll_dir in dll_dirs:
        dll_dir = dll_dir.resolve()
        if not dll_dir.exists() or dll_dir in added:
            continue
        _add_dll_directory(dll_dir)
        added.append(dll_dir)

    global _LAST_RUNTIME_PATHS
    _LAST_RUNTIME_PATHS = added
    return added


def _external_package_dirs(project_ocr_dir: Path) -> list[Path]:
    project_dir = project_ocr_dir.parent
    return [
        project_dir / "site-packages",
        project_dir / "python_packages",
        project_dir / "python",
        project_ocr_dir / "site-packages",
        project_ocr_dir / "python_packages",
        project_ocr_dir / "python",
    ]


def _site_package_roots() -> list[Path]:
    roots: list[Path] = []
    for value in [site.getusersitepackages(), *site.getsitepackages()]:
        if not value:
            continue
        path = Path(value)
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _add_dll_directory(path: Path) -> None:
    path_text = str(path)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if path_text not in parts:
        os.environ["PATH"] = path_text + os.pathsep + os.environ.get("PATH", "")

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        try:
            _DLL_DIR_HANDLES.append(os.add_dll_directory(path_text))
        except OSError:
            pass


def ensure_project_ocr_models(project_ocr_dir: str | Path) -> Path:
    project_ocr_dir = Path(project_ocr_dir).resolve()
    model_dir = project_ocr_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    bundled_source = _bundled_ocr_model_dir()
    if bundled_source.exists():
        _copy_missing_models(bundled_source, model_dir)

    source = Path.home() / ".EasyOCR" / "model"
    if source.exists():
        _copy_missing_models(source, model_dir)

    return model_dir


def _bundled_ocr_model_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return Path()
    mei_path = getattr(sys, "_MEIPASS", "")
    if not mei_path:
        return Path()
    return Path(mei_path) / "ocr" / "model"


def _copy_missing_models(source: Path, target_dir: Path) -> None:
    for model_file in source.glob("*.pth"):
        target = target_dir / model_file.name
        if not target.exists():
            shutil.copy2(model_file, target)


def similarity_percent(needle: str, haystack: str) -> int:
    left = normalize_id(needle)
    right = normalize_id(haystack)
    if not left or not right:
        return 0
    if left in right or right in left:
        return 100

    tokens = [token for token in re.split(r"\s+", right) if token]
    candidates = tokens + [right]
    score = max(SequenceMatcher(None, left, candidate).ratio() for candidate in candidates)
    return int(round(score * 100))


def find_id_matches(
    ocr_text: str,
    players: list[dict[str, Any]],
    threshold: int,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    threshold = max(1, min(100, int(threshold)))
    for player in players:
        steam_id = str(player.get("steam_id_text") or "")
        score = similarity_percent(steam_id, ocr_text)
        if score >= threshold:
            match = dict(player)
            match["score"] = score
            matches.append(match)

    matches.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return matches


def find_exact_digit_matches(
    ocr_text: str,
    players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    digit_parts = re.findall(r"\d+", str(ocr_text or ""))
    candidates = set(digit_parts)
    if digit_parts:
        candidates.add("".join(digit_parts))

    matches: list[dict[str, Any]] = []
    for player in players:
        steam_id = extract_digits(str(player.get("steam_id_text") or ""))
        if not steam_id:
            continue
        if steam_id in candidates:
            match = dict(player)
            match["score"] = 100
            matches.append(match)

    matches.sort(key=lambda item: str(item.get("steam_id_text") or ""))
    return matches


class OcrService:
    def __init__(self, project_ocr_dir: str | Path):
        self.project_ocr_dir = Path(project_ocr_dir).resolve()
        prepare_ocr_runtime(self.project_ocr_dir)
        self.model_dir = ensure_project_ocr_models(self.project_ocr_dir)
        self._reader: Any = None
        self._languages: tuple[str, ...] = ()

    def read_region(self, region: dict[str, Any], languages: list[str] | tuple[str, ...]) -> str:
        image = self._screenshot(region)
        try:
            reader = self._get_reader(languages)
            results = reader.readtext(np.array(image), detail=0, paragraph=True)
            return "\n".join(str(part) for part in results if str(part).strip())
        except OcrDependencyError as easyocr_error:
            fallback = self._read_digits_with_tesseract(image)
            if fallback:
                return fallback
            raise OcrDependencyError(self._combined_tesseract_error(easyocr_error)) from easyocr_error

    def read_digits_region(self, region: dict[str, Any], languages: list[str] | tuple[str, ...]) -> str:
        image = self._screenshot(region)
        tesseract_exe = self._find_tesseract_exe()
        if tesseract_exe:
            return self._read_digits_with_tesseract(image, tesseract_exe, raise_on_error=True)

        try:
            return self._read_digits_with_easyocr(image, languages)
        except OcrDependencyError as easyocr_error:
            raise OcrDependencyError(self._combined_tesseract_error(easyocr_error)) from easyocr_error

    def _read_digits_with_easyocr(self, image: Image.Image, languages: list[str] | tuple[str, ...]) -> str:
        reader = self._get_reader(languages)
        texts: list[str] = []

        for candidate in self._digit_image_candidates(image):
            try:
                results = reader.readtext(
                    np.array(candidate),
                    detail=0,
                    paragraph=False,
                    allowlist="0123456789",
                    decoder="greedy",
                    batch_size=1,
                )
            except TypeError:
                results = reader.readtext(np.array(candidate), detail=0, paragraph=False)

            text = " ".join(str(part) for part in results if str(part).strip())
            if text:
                texts.append(text)
            digits = extract_digits(text)
            if digits:
                return digits

        return extract_digits(" ".join(texts)) or " ".join(texts)

    def _read_digits_with_tesseract(
        self,
        image: Image.Image,
        tesseract_exe: Path | None = None,
        *,
        raise_on_error: bool = False,
    ) -> str:
        tesseract_exe = tesseract_exe or self._find_tesseract_exe()
        if not tesseract_exe:
            return ""

        errors: list[str] = []
        for candidate in self._digit_image_candidates(image):
            for psm in ("7", "6", "13"):
                try:
                    text = self._run_tesseract(tesseract_exe, candidate, psm)
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                digits = extract_digits(text)
                if digits:
                    return digits

        if errors and raise_on_error:
            unique_errors = list(dict.fromkeys(errors))
            detail = "\n".join(f"- {error}" for error in unique_errors[:4])
            raise OcrDependencyError(f"Tesseract OCR найден, но не запустился:\n{detail}")

        return ""

    def _run_tesseract(self, tesseract_exe: Path, image: Image.Image, psm: str) -> str:
        env = os.environ.copy()
        tessdata = tesseract_exe.parent / "tessdata"
        if tessdata.exists():
            env["TESSDATA_PREFIX"] = str(tesseract_exe.parent)
        env["PATH"] = str(tesseract_exe.parent) + os.pathsep + env.get("PATH", "")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            image_path = Path(fh.name)
        try:
            image.save(image_path)
            cmd = [
                str(tesseract_exe),
                str(image_path),
                "stdout",
                "--psm",
                psm,
                "-l",
                "eng",
                "--tessdata-dir",
                str(tessdata),
                "-c",
                "tessedit_char_whitelist=0123456789",
            ]
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=8,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0:
                raise OcrDependencyError(completed.stderr.strip() or "Tesseract OCR вернул ошибку.")
            return completed.stdout.strip()
        finally:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _find_tesseract_exe(self) -> Path | None:
        for candidate in self._tesseract_candidates():
            if candidate.exists():
                return candidate.resolve()

        from_path = shutil.which("tesseract")
        return Path(from_path).resolve() if from_path else None

    def _tesseract_candidates(self) -> list[Path]:
        program_files = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
        candidates = [
            self.project_ocr_dir / "tesseract" / "tesseract.exe",
            self.project_ocr_dir / "Tesseract-OCR" / "tesseract.exe",
            self.project_ocr_dir / "tesseract.exe",
            self.project_ocr_dir.parent / "tesseract" / "tesseract.exe",
            self.project_ocr_dir.parent / "Tesseract-OCR" / "tesseract.exe",
        ]
        if getattr(sys, "frozen", False):
            mei_path = getattr(sys, "_MEIPASS", "")
            if mei_path:
                bundled_ocr = Path(mei_path) / "ocr"
                candidates.extend(
                    [
                        bundled_ocr / "tesseract" / "tesseract.exe",
                        bundled_ocr / "Tesseract-OCR" / "tesseract.exe",
                        Path(mei_path) / "tesseract" / "tesseract.exe",
                        Path(mei_path) / "Tesseract-OCR" / "tesseract.exe",
                    ]
                )
        for folder in program_files:
            if folder:
                candidates.append(Path(folder) / "Tesseract-OCR" / "tesseract.exe")
        return candidates

    def _combined_tesseract_error(self, easyocr_error: Exception) -> str:
        checked = "\n".join(f"- {path}" for path in self._tesseract_candidates())
        return (
            f"{easyocr_error}\n\n"
            "Fallback OCR без Torch тоже недоступен: tesseract.exe не найден.\n\n"
            f"Проверенные пути Tesseract:\n{checked}\n\n"
            "Чтобы OCR работал в onefile exe, положи portable Tesseract рядом с exe:\n"
            "ocr\\tesseract\\tesseract.exe\n"
            "ocr\\tesseract\\tessdata\\eng.traineddata\n\n"
            f"Ссылка установки Tesseract OCR:\n{TESSERACT_INSTALL_URL}"
        )

    def _screenshot(self, region: dict[str, Any]) -> Image.Image:
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        width = max(1, int(region.get("width", 1)))
        height = max(1, int(region.get("height", 1)))
        return pyautogui.screenshot(region=(x, y, width, height))

    def _digit_image_candidates(self, image: Image.Image) -> list[Image.Image]:
        gray = ImageOps.grayscale(image)
        contrast = ImageOps.autocontrast(gray)
        width, height = contrast.size
        candidates = [
            image,
            contrast,
            contrast.resize((width * 2, height * 2), Image.Resampling.LANCZOS),
            contrast.resize((width * 3, height * 3), Image.Resampling.LANCZOS),
        ]
        return candidates

    def _get_reader(self, languages: list[str] | tuple[str, ...]):
        normalized = tuple(lang.strip().lower() for lang in languages if str(lang).strip())
        if not normalized:
            normalized = ("ru",)
        if self._reader is not None and normalized == self._languages:
            return self._reader

        prepare_ocr_runtime(self.project_ocr_dir)
        self._require_models()
        try:
            import easyocr
        except Exception as exc:
            raise OcrDependencyError(_format_dependency_error(exc)) from exc

        try:
            self._reader = easyocr.Reader(
                list(normalized),
                gpu=False,
                model_storage_directory=str(self.model_dir),
                user_network_directory=str(self.project_ocr_dir / "user_network"),
                download_enabled=False,
                verbose=False,
            )
        except Exception as exc:
            raise OcrDependencyError(_format_dependency_error(exc)) from exc
        self._languages = normalized
        return self._reader

    def _require_models(self) -> None:
        models = {path.name for path in self.model_dir.glob("*.pth")}
        missing = [name for name in ("craft_mlt_25k.pth", "cyrillic_g2.pth") if name not in models]
        if missing:
            raise OcrDependencyError(
                "OCR модели не найдены рядом с приложением.\n\n"
                f"Папка моделей: {self.model_dir}\n"
                f"Не хватает: {', '.join(missing)}\n\n"
                "Скопируй папку ocr рядом с Dota2Tracker.exe так, чтобы получилось:\n"
                "Dota2Tracker.exe\n"
                "ocr\\model\\craft_mlt_25k.pth\n"
                "ocr\\model\\cyrillic_g2.pth"
            )


def _format_dependency_error(exc: Exception) -> str:
    runtime_paths = "\n".join(f"- {path}" for path in _LAST_RUNTIME_PATHS[:8])
    if not runtime_paths:
        runtime_paths = "- пути DLL не найдены"
    return (
        "EasyOCR/Torch не запустился. Приложение продолжает работать без OCR.\n\n"
        "Модели OCR берутся из папки проекта: ocr\\model. "
        "Ошибка ниже относится к DLL библиотеки Torch, а не к файлам моделей OCR.\n\n"
        f"Пути DLL, которые были добавлены:\n{runtime_paths}\n\n"
        f"{exc}"
    )
