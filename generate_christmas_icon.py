#!/usr/bin/env python3
"""
Generate Christmas version of Spot icon with tonttulakki (Santa hat).
Creates spot-192-christmas.png and spot-512-christmas.png with hat on top.
"""

from PIL import Image, ImageDraw


def add_christmas_hat(img, hat_height_ratio=0.2):
    """
    Add a Christmas hat (tonttulakki) to the top of the image.
    Uses a downloaded hat image from Pixabay for better quality.
    
    Args:
        img: PIL Image object
        hat_height_ratio: Ratio of hat height to image height (default 0.2 = 20%, smaller)
    """
    from PIL import ImageOps
    
    width, height = img.size
    
    # Try to load the hat template image
    try:
        hat_template = Image.open("static/santa-hat-template.png")
        hat_template = hat_template.convert("RGBA")
    except FileNotFoundError:
        print("Warning: santa-hat-template.png not found, skipping hat")
        return img
    
    # Crop to get only the first hat in the upper-left corner
    # Assuming 6 hats arranged in a grid (2 rows x 3 columns or 3 rows x 2 columns)
    template_width, template_height = hat_template.size
    
    # Try to detect the grid - likely 2x3 or 3x2
    # For 2x3: each hat is template_width/3 wide, template_height/2 tall
    # For 3x2: each hat is template_width/2 wide, template_height/3 tall
    # Let's try 2x3 first (more common for horizontal layouts)
    hat_crop_width = template_width // 3
    hat_crop_height = template_height // 2
    
    # Crop the upper-left hat
    hat_cropped = hat_template.crop((0, 0, hat_crop_width, hat_crop_height))
    
    # Calculate hat dimensions for final image - make it smaller
    hat_height = int(height * hat_height_ratio)
    # Maintain aspect ratio of the cropped hat
    hat_aspect = hat_crop_width / hat_crop_height
    hat_width = int(hat_height * hat_aspect)
    
    # Resize hat to fit
    hat_resized = hat_cropped.resize(
        (hat_width, hat_height),
        Image.Resampling.LANCZOS
    )
    
    # Rotate clockwise (positive angle = CW)
    # Rotate by 15 to 25 degrees for a natural tilted look
    hat_rotated = hat_resized.rotate(20, expand=True, resample=Image.Resampling.BICUBIC)
    
    # Get the rotated hat dimensions
    rotated_width, rotated_height = hat_rotated.size
    
    # Calculate position - offset left and down to position on the dog's head
    # The hat should sit naturally on top of the dog's head
    center_x = width // 2
    # Move left from center
    hat_x = center_x - rotated_width // 2 - int(width * 0.08)  # Shift left
    # Position on the head - account for rotation
    hat_y = int(height * 0.06) - int(rotated_height * 0.15)  # Position on head, slightly up
    
    # Create a mask from the hat's alpha channel for proper blending
    if hat_rotated.mode == 'RGBA':
        hat_mask = hat_rotated.split()[3]  # Get alpha channel
    else:
        hat_mask = None
    
    # Paste the hat onto the image
    img.paste(hat_rotated, (hat_x, hat_y), hat_mask if hat_mask else None)
    
    return img


def create_christmas_icon(original_path, output_path, hat_height_ratio=0.2):
    """
    Load original icon, add space at top, and draw Christmas hat.
    
    Args:
        original_path: Path to original icon file
        output_path: Path to save Christmas version
        hat_height_ratio: Ratio of hat height to original image height
    """
    # Load original image
    original = Image.open(original_path)
    orig_width, orig_height = original.size
    
    # Calculate new dimensions with space for hat
    hat_height = int(orig_height * hat_height_ratio)
    new_height = orig_height + hat_height
    new_width = orig_width
    
    # Create new image with transparent background (or black if no alpha)
    if original.mode == 'RGBA':
        new_img = Image.new('RGBA', (new_width, new_height), (0, 0, 0, 0))
    else:
        new_img = Image.new('RGB', (new_width, new_height), (0, 0, 0))
        original = original.convert('RGB')
    
    # Paste original image at the bottom (leaving space at top for hat)
    new_img.paste(original, (0, hat_height))
    
    # Add Christmas hat
    add_christmas_hat(new_img, hat_height_ratio)
    
    # Save the result
    new_img.save(output_path, format="PNG", optimize=True)
    print(f"✓ Created {output_path} ({new_width}x{new_height})")


def main():
    """Generate Christmas versions of both icon sizes."""
    sizes = [192, 512]
    
    for size in sizes:
        original_path = f"static/spot-{size}.png"
        output_path = f"static/spot-{size}-christmas.png"
        
        print(f"Generating spot-{size}-christmas.png...")
        try:
            create_christmas_icon(original_path, output_path)
        except FileNotFoundError:
            print(f"✗ Error: {original_path} not found")
        except Exception as e:
            print(f"✗ Error creating {output_path}: {e}")
    
    print("\nChristmas icons generated successfully!")


if __name__ == "__main__":
    try:
        main()
    except ImportError:
        print("Error: Pillow (PIL) is required to generate icons.")
        print("Install it with: uv pip install Pillow")
        exit(1)

