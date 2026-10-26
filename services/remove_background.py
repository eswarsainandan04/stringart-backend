"""Background Removal Module for String Art Image Processing Pipeline.

Uses lightweight U2NetP (or other selectable models such as 'bria-rmbg', 'silueta')
to isolate subjects and output transparent PNG images.
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union
import numpy as np
from PIL import Image, ImageDraw
import rembg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("BackgroundRemover")

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


class BackgroundRemover:
    """Manages AI-driven background removal with session caching."""

    def __init__(
        self,
        model_name: str = "u2net_human_seg",
        alpha_matting: bool = False,
        alpha_matting_foreground_threshold: int = 240,
        alpha_matting_background_threshold: int = 10,
        alpha_matting_erode_size: int = 10,
    ) -> None:
        """Initializes the background remover and preloads the model session.

        Args:
            model_name: Name of the rembg model (default: "u2netp" for ultra-lightweight ~4.5MB).
            alpha_matting: Whether to use post-processing alpha matting.
            alpha_matting_foreground_threshold: Threshold for foreground matting.
            alpha_matting_background_threshold: Threshold for background matting.
            alpha_matting_erode_size: Erosion size for matting.
        """
        self.model_name = model_name
        self.alpha_matting = alpha_matting
        self.alpha_matting_foreground_threshold = alpha_matting_foreground_threshold
        self.alpha_matting_background_threshold = alpha_matting_background_threshold
        self.alpha_matting_erode_size = alpha_matting_erode_size

        logger.info("Initializing session with model: '%s'...", self.model_name)
        start_time = time.perf_counter()
        self.session = rembg.new_session(self.model_name)
        init_time = (time.perf_counter() - start_time) * 1000
        logger.info("Model session ready (initialized in %.1f ms).", init_time)

    def remove_background(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
    ) -> Image.Image:
        """Removes the background from the provided image and returns an RGBA PIL Image.

        Args:
            image_input: File path, PIL Image, or NumPy ndarray.

        Returns:
            PIL Image in 'RGBA' mode with the background removed.
        """
        # Convert path to PIL Image if string/Path
        if isinstance(image_input, (str, Path)):
            pil_image = Image.open(str(image_input))
        elif isinstance(image_input, np.ndarray):
            pil_image = Image.fromarray(image_input)
        elif isinstance(image_input, Image.Image):
            pil_image = image_input
        else:
            raise TypeError(f"Unsupported image input type: {type(image_input)}")

        # Ensure image is in RGB or RGBA mode for rembg
        if pil_image.mode not in ("RGB", "RGBA"):
            pil_image = pil_image.convert("RGB")

        # Run background removal
        result: Image.Image = rembg.remove(
            pil_image,
            session=self.session,
            alpha_matting=self.alpha_matting,
            alpha_matting_foreground_threshold=self.alpha_matting_foreground_threshold,
            alpha_matting_background_threshold=self.alpha_matting_background_threshold,
            alpha_matting_erode_size=self.alpha_matting_erode_size,
        )

        return result

    def process_circular_image(
        self,
        framed_image: Union[str, Path, Image.Image, np.ndarray],
        bg_color: Tuple[int, int, int, int] = (237, 237, 237, 255),
        target_size: int = 800,
        generate_svg: bool = False,
    ) -> Tuple[Image.Image, str]:
        """Removes background from a circular framed image, composites onto a light grey
        circular background, and optionally generates SVG markup.

        Args:
            framed_image: PIL Image or path to image framed into circle.
            bg_color: RGBA color for the circular interior background (default: #EDEDED light grey).
            target_size: Output resolution (default: 800x800).
            generate_svg: Whether to generate base64 SVG markup (default: False).

        Returns:
            Tuple of (final_pil_image, svg_content_string).
        """
        # 1. AI background removal
        cutout = self.remove_background(framed_image)
        if cutout.size != (target_size, target_size):
            cutout = cutout.resize((target_size, target_size), Image.Resampling.LANCZOS)
        if cutout.mode != "RGBA":
            cutout = cutout.convert("RGBA")

        # 2. Circular background layer with light grey fill
        bg_layer = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg_layer)
        bg_draw.ellipse((0, 0, target_size, target_size), fill=bg_color)

        # 3. Composite subject cutout over light grey circular background
        composite = Image.alpha_composite(bg_layer, cutout)

        # 4. Strictly enforce circular boundary mask (alpha=0 outside circle)
        mask = Image.new("L", (target_size, target_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, target_size, target_size), fill=255)

        final_image = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
        final_image.paste(composite, (0, 0), mask)

        # 5. Optional SVG generation
        svg_content = ""
        if generate_svg:
            buffer = io.BytesIO()
            final_image.save(buffer, format="PNG")
            b64_png = base64.b64encode(buffer.getvalue()).decode("utf-8")

            radius = target_size / 2.0
            hex_color = f"#{bg_color[0]:02x}{bg_color[1]:02x}{bg_color[2]:02x}"
            svg_content = (
                f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'viewBox="0 0 {target_size} {target_size}" width="{target_size}" height="{target_size}">\n'
                f'  <defs>\n'
                f'    <clipPath id="circularCrop">\n'
                f'      <circle cx="{radius}" cy="{radius}" r="{radius}" />\n'
                f'    </clipPath>\n'
                f'  </defs>\n'
                f'  <!-- Circular Light Grey Background -->\n'
                f'  <circle cx="{radius}" cy="{radius}" r="{radius}" fill="{hex_color}" />\n'
                f'  <!-- AI Background-Removed Circular Subject -->\n'
                f'  <image href="data:image/png;base64,{b64_png}" width="{target_size}" height="{target_size}" clip-path="url(#circularCrop)" />\n'
                f'</svg>'
            )

        return final_image, svg_content

    def process_file(
        self,
        input_file: Union[str, Path],
        output_file: Optional[Union[str, Path]] = None,
    ) -> Path:
        """Processes a single image file and saves the RGBA result as PNG.

        Args:
            input_file: Path to the source image.
            output_file: Optional path to save the output. If None, saves next to input as <name>_no_bg.png.

        Returns:
            Path to the saved PNG file.
        """
        input_path = Path(input_file).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        if output_file is None:
            output_path = input_path.with_name(f"{input_path.stem}_no_bg.png")
        else:
            output_path = Path(output_file).resolve()
            if output_path.suffix.lower() != ".png":
                output_path = output_path.with_suffix(".png")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Processing: %s", input_path.name)
        start_time = time.perf_counter()
        no_bg_image = self.remove_background(input_path)
        no_bg_image.save(output_path, format="PNG")
        elapsed = (time.perf_counter() - start_time) * 1000

        logger.info("Saved transparent image to: %s (took %.1f ms)", output_path.name, elapsed)
        return output_path

    def process_directory(
        self,
        input_dir: Union[str, Path],
        output_dir: Union[str, Path],
    ) -> List[Path]:
        """Processes all supported images inside input_dir and saves them to output_dir.

        Args:
            input_dir: Directory containing input images.
            output_dir: Directory to save processed images.

        Returns:
            List of Paths to generated output files.
        """
        in_path = Path(input_dir).resolve()
        out_path = Path(output_dir).resolve()

        if not in_path.exists() or not in_path.is_dir():
            raise FileNotFoundError(f"Input directory not found: {in_path}")

        out_path.mkdir(parents=True, exist_ok=True)

        image_files = [
            f for f in in_path.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ]

        if not image_files:
            logger.warning("No supported image files found in %s", in_path)
            return []

        logger.info("Found %d image(s) to process in %s", len(image_files), in_path)
        processed_files: List[Path] = []

        total_start = time.perf_counter()
        for idx, file_path in enumerate(image_files, start=1):
            target_out = out_path / f"{file_path.stem}.png"
            logger.info("[%d/%d] Processing %s...", idx, len(image_files), file_path.name)
            out_file = self.process_file(file_path, target_out)
            processed_files.append(out_file)


        total_elapsed = time.perf_counter() - total_start
        logger.info(
            "Batch complete: processed %d image(s) in %.2f seconds.",
            len(processed_files),
            total_elapsed,
        )
        return processed_files


def frame_and_crop_circle(
    image_input: Union[str, Path, Image.Image],
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    scale: float = 1.0,
    canvas_size: float = 340.0,
    target_size: int = 800,
) -> Image.Image:
    """Reconstructs user framing from mobile UI coordinates onto a target square canvas
    and masks it strictly to the circular frame.
    """
    if isinstance(image_input, (str, Path)):
        img = Image.open(str(image_input))
    elif isinstance(image_input, Image.Image):
        img = image_input
    else:
        raise TypeError(f"Unsupported image input type: {type(image_input)}")

    if img.mode != "RGBA":
        img = img.convert("RGBA")

    img_w, img_h = img.size
    aspect = (img_w / img_h) if img_h > 0 else 1.0

    if aspect >= 1.0:
        disp_w = canvas_size
        disp_h = canvas_size / aspect
    else:
        disp_h = canvas_size
        disp_w = canvas_size * aspect

    ratio = target_size / canvas_size
    target_w = max(1, int(round(disp_w * scale * ratio)))
    target_h = max(1, int(round(disp_h * scale * ratio)))

    center_x = (target_size / 2.0) + (pan_x * ratio)
    center_y = (target_size / 2.0) + (pan_y * ratio)

    img_x0 = int(round(center_x - target_w / 2.0))
    img_y0 = int(round(center_y - target_h / 2.0))

    resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
    canvas.paste(resized, (img_x0, img_y0), resized)

    mask = Image.new("L", (target_size, target_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, target_size, target_size), fill=255)

    circular_canvas = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
    circular_canvas.paste(canvas, (0, 0), mask)

    return circular_canvas


def add_light_grey_background(
    transparent_cutout: Image.Image,
    bg_color: Tuple[int, int, int] = (237, 237, 237),
    target_size: int = 800,
) -> Image.Image:
    """Step 4 of pipeline: Receives the transparent background-less image (RGBA)
    and composites it onto a clean circular light grey (#EDEDED) background.

    Args:
        transparent_cutout: Transparent background-removed PIL Image (RGBA).
        bg_color: RGB tuple for background color (default: (237, 237, 237) for #EDEDED).
        target_size: Resolution of the output square canvas (default: 800x800).

    Returns:
        RGBA PIL Image composited on circular light grey background with transparent outside corners.
    """
    if transparent_cutout.size != (target_size, target_size):
        transparent_cutout = transparent_cutout.resize((target_size, target_size), Image.Resampling.LANCZOS)

    if transparent_cutout.mode != "RGBA":
        transparent_cutout = transparent_cutout.convert("RGBA")

    # 1. Circular background layer with light grey fill and transparent corners
    bg_layer = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_layer)
    rgba_bg = (bg_color[0], bg_color[1], bg_color[2], 255)
    bg_draw.ellipse((0, 0, target_size, target_size), fill=rgba_bg)

    # 2. Composite subject cutout over light grey circular background
    composite = Image.alpha_composite(bg_layer, transparent_cutout)

    # 3. Strictly enforce circular boundary mask (alpha=0 outside circle, zero squared edges)
    mask = Image.new("L", (target_size, target_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, target_size, target_size), fill=255)

    final_canvas = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
    final_canvas.paste(composite, (0, 0), mask=mask)

    return final_canvas


_shared_remover: Optional[BackgroundRemover] = None


def get_background_remover(model_name: str = "u2net_human_seg") -> BackgroundRemover:
    """Returns a shared BackgroundRemover instance to reuse the preloaded ONNX model session."""
    global _shared_remover
    if _shared_remover is None or _shared_remover.model_name != model_name:
        _shared_remover = BackgroundRemover(model_name=model_name)
    return _shared_remover


def process_circular_subject(
    image_input: Union[str, Path, Image.Image],
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    scale: float = 1.0,
    canvas_size: float = 340.0,
    target_size: int = 800,
    bg_color: Tuple[int, int, int] = (237, 237, 237),
    model_name: str = "u2net_human_seg",
    generate_svg: bool = False,
) -> Tuple[Image.Image, str]:
    """Complete 5-step human portrait pipeline:
    1. User clicks generate with coordinates (pan_x, pan_y, scale, canvas_size).
    2. Extract only the focused human subject from full image using u2net_human_seg.
    3. Crop/frame transparent human cutout into circular frame using user framing coordinates.
    4. Composite subject onto clean light-grey circular background (#EDEDED).
    5. Returns RGBA image with circular art and transparent corners.
    """
    remover = get_background_remover(model_name=model_name)

    # Step 1: Human segmentation on full original photo (avoids artificial circular crop distortions)
    cutout = remover.remove_background(image_input)

    # Step 2: Frame and crop extracted human subject into circular frame using user coordinates
    framed = frame_and_crop_circle(
        image_input=cutout,
        pan_x=pan_x,
        pan_y=pan_y,
        scale=scale,
        canvas_size=canvas_size,
        target_size=target_size,
    )

    # Step 3: Add light grey circular background to transparent cutout
    final_image = add_light_grey_background(
        transparent_cutout=framed,
        bg_color=bg_color,
        target_size=target_size,
    )

    return final_image, ""


def remove_background(
    image: Union[str, Path, Image.Image, np.ndarray],
    model_name: str = "u2net",
) -> Image.Image:
    """Convenience functional wrapper for quick one-off background removal."""
    remover = BackgroundRemover(model_name=model_name)
    return remover.remove_background(image)


def main() -> None:
    """CLI handler for background removal."""
    base_dir = Path(__file__).resolve().parent
    default_input = base_dir / "input"
    default_output = base_dir / "output"

    parser = argparse.ArgumentParser(
        description="Remove image background using u2net (or custom model)."
    )
    parser.add_argument(
        "-i", "--input",
        type=str,
        default=str(default_input),
        help=f"Path to input image file or directory (default: {default_input})",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=str(default_output),
        help=f"Path to output image file or directory (default: {default_output})",
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
        default="u2net",
        help="Rembg model name ('u2net', 'u2netp', 'bria-rmbg', 'silueta', etc. default: 'u2net')",
    )
    parser.add_argument(
        "--alpha-matting",
        action="store_true",
        help="Enable alpha matting for smoother boundary transitions.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not input_path.exists():
        logger.error("Input path '%s' does not exist.", input_path)
        sys.exit(1)

    remover = BackgroundRemover(model_name=args.model, alpha_matting=args.alpha_matting)

    if input_path.is_file():
        # Single file processing
        if output_path.is_dir() or output_path.suffix == "":
            output_file = output_path / f"{input_path.stem}.png"
        else:
            output_file = output_path
        remover.process_file(input_path, output_file)
    else:
        # Directory batch processing
        remover.process_directory(input_path, output_path)


if __name__ == "__main__":
    main()
