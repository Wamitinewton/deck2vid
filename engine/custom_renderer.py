import re
from PIL import Image, ImageDraw, ImageFont
from engine.caption_styles import StyleSpec
from engine.sync import WordTimestamp

def _is_punct(word: str) -> bool:
    return bool(re.match(r'^[.,!?;:\-—]+$', word))

def _group_words_into_phrases(words: list[WordTimestamp], max_words=5) -> list[list[WordTimestamp]]:
    groups, current = [], []
    for wt in words:
        current.append(wt)
        if len(current) >= max_words or _is_punct(wt["word"]):
            groups.append(current)
            current = []
    if current:
        if groups and len(current) <= 2:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups

class FrameRenderer:
    """Renders frame-accurate karaoke captions onto Pillow images."""
    def __init__(self, base_image: Image.Image, words: list[WordTimestamp], style: StyleSpec, font: ImageFont.FreeTypeFont):
        self.base_image = base_image.convert("RGBA")
        self.groups = _group_words_into_phrases(words)
        self.style = style
        self.font = font
        self.width, self.height = base_image.size
        
        # Pre-calculate space width
        self.space_width = 0
        if hasattr(self.font, "getlength"):
            self.space_width = self.font.getlength(" ")
        else:
            self.space_width = 15 # Fallback
            
    def render(self, time_ms: float) -> Image.Image:
        # 1. Identify which phrase is active right now
        active_group = None
        for i, group in enumerate(self.groups):
            start = group[0]['offset_ms']
            end = group[-1]['offset_ms'] + group[-1]['duration_ms'] + 150 # Buffer
            
            # Prevent overlap with next phrase
            if i + 1 < len(self.groups):
                next_start = self.groups[i+1][0]['offset_ms']
                end = min(end, next_start - 10)
                
            if start <= time_ms <= end:
                active_group = group
                break
                
        frame = self.base_image.copy()
        if not active_group:
            return frame.convert("RGB")
            
        draw = ImageDraw.Draw(frame, "RGBA")
        
        # 2. Measure phrase width for perfect bottom-centering
        total_width = 0
        word_elements = []
        
        for idx, wt in enumerate(active_group):
            word = wt['word']
            w = self.font.getlength(word) if hasattr(self.font, "getlength") else 20
            
            add_space = not _is_punct(word) and idx > 0
            if add_space:
                total_width += self.space_width
                
            word_elements.append({
                'word': word,
                'w': w,
                'add_space': add_space,
                'start': wt['offset_ms'],
                'end': wt['offset_ms'] + wt['duration_ms']
            })
            total_width += w
            
        # 3. Draw Background Box (TikTok shadow style)
        box_pad_x = 25
        box_pad_y = 15
        bbox = draw.textbbox((0,0), "Hgy", font=self.font)
        text_height = bbox[3] - bbox[1]
        
        start_x = (self.width - total_width) / 2
        start_y = self.height - self.style.margin_bottom - text_height
        
        x0 = start_x - box_pad_x
        y0 = start_y - box_pad_y
        x1 = start_x + total_width + box_pad_x
        y1 = start_y + text_height + box_pad_y
        
        # Rounded corners for the background
        draw.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=self.style.bg_color)
        
        # 4. Draw text (Karaoke Highlight)
        cursor_x = start_x
        for elem in word_elements:
            if elem['add_space']:
                cursor_x += self.space_width
                
            # Allow slight persistence of the highlight (100ms) for smoother visual flow
            is_spoken = elem['start'] <= time_ms <= (elem['end'] + 100) 
            color = self.style.primary_color if is_spoken else self.style.secondary_color
            
            draw.text(
                (cursor_x, start_y), 
                elem['word'], 
                font=self.font, 
                fill=color,
                stroke_width=self.style.outline_width,
                stroke_fill=self.style.outline_color
            )
            cursor_x += elem['w']
            
        return frame.convert("RGB")