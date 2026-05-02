import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
import io


def preprocess_image(image: Image.Image) -> Image.Image:
    """Apply preprocessing to improve OCR accuracy."""
    img = image.convert("L")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def extract_text(image: Image.Image) -> dict:
    """Run OCR on an image and return text with confidence."""
    processed = preprocess_image(image)

    data = pytesseract.image_to_data(processed, output_type=pytesseract.Output.DICT)

    words = []
    confidences = []
    for i, word in enumerate(data["text"]):
        conf = int(data["conf"][i])
        if conf > 0 and word.strip():
            words.append(word)
            confidences.append(conf)

    text = " ".join(words)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    return {
        "text": text,
        "confidence": round(avg_confidence, 2),
        "word_count": len(words),
    }


def extract_text_from_bytes(image_bytes: bytes) -> dict:
    """Run OCR on raw image bytes."""
    image = Image.open(io.BytesIO(image_bytes))
    return extract_text(image)
