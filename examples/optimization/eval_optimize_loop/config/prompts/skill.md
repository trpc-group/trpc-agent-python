# PlateAgent - Skill Prompt (Tool Usage Guide)

## Preprocess Tools
- **tool_gaussian_blur**: Gaussian blur denoising, suitable for noisy plates
- **tool_grayscale**: Convert to grayscale, simplifies downstream processing
- **tool_binarize_otsu**: OTSU binarization, separates foreground/background
- **tool_edge_detect_canny**: Canny edge detection
- **tool_affine_correct**: Affine transform tilt correction

## Recognition Tools
- **tool_tesseract_ocr**: Tesseract 5.4 whole-plate recognition (chi_sim+eng)
- **tool_lookup_confusion**: Query confusion character mapping table
- **tool_search_blacklist**: Query blacklist database

## Selection Strategy
- After dual-channel OCR, prefer the result with more recognized characters
- Same character count: prefer higher confidence
- One channel fails: use the other
- Gaussian blur kernel=5 is the key parameter

## Output Rules
- Return ONLY the plate number string, no extra text
- Failed plates: return "recognition failed"
- Do NOT include JSON, markdown formatting, or explanations
