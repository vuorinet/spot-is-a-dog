#!/usr/bin/env python3
"""
Generate PWA icon files for Spot is a dog app.
Creates icon-192.png and icon-512.png with lightning and chart bars design.
"""

from PIL import Image, ImageDraw


def create_icon(size):
    """Create an icon with lightning bolt and chart bars representing electricity prices."""
    # Create image with black background
    img = Image.new("RGB", (size, size), color="#000000")
    draw = ImageDraw.Draw(img)

    # Center point
    center_x, center_y = size // 2, size // 2

    # Draw chart bars (representing price bars)
    bar_width = size // 12
    bar_spacing = size // 20
    base_y = size * 0.7  # Base of bars
    num_bars = 4

    # Create bars with varying heights (green, yellow, red)
    bar_colors = [
        "#2ecc71",
        "#f1c40f",
        "#e74c3c",
        "#2ecc71",
    ]  # Green, Yellow, Red, Green
    bar_heights = [0.4, 0.6, 0.8, 0.5]  # Relative heights

    bar_start_x = center_x - ((num_bars * (bar_width + bar_spacing) - bar_spacing) // 2)

    for i in range(num_bars):
        x = bar_start_x + i * (bar_width + bar_spacing)
        bar_height = size * 0.15 * bar_heights[i]
        y_top = base_y - bar_height

        # Draw bar
        draw.rectangle(
            [x, y_top, x + bar_width, base_y],
            fill=bar_colors[i],
            outline=bar_colors[i],
        )

    # Draw lightning bolt (Z-shaped)
    bolt_color = "#ffcc00"  # Yellow/gold lightning
    bolt_width = size // 16
    bolt_size = size * 0.35

    # Lightning path (Z shape, more jagged)
    bolt_x = center_x
    bolt_y = center_y - size * 0.15

    # Calculate points for jagged lightning
    points = [
        (bolt_x - bolt_size * 0.15, bolt_y - bolt_size * 0.4),
        (bolt_x + bolt_size * 0.2, bolt_y - bolt_size * 0.2),
        (bolt_x - bolt_size * 0.1, bolt_y),
        (bolt_x + bolt_size * 0.25, bolt_y + bolt_size * 0.3),
        (bolt_x - bolt_size * 0.05, bolt_y + bolt_size * 0.45),
    ]

    # Draw lightning as filled polygon with outline
    draw.polygon(points, fill=bolt_color, outline=bolt_color)

    # Add glow effect by drawing slightly larger bolt underneath
    glow_points = [
        (
            p[0] + (1 if i % 2 == 0 else -1) * bolt_width * 0.3,
            p[1] + (1 if i % 2 == 0 else -1) * bolt_width * 0.3,
        )
        for i, p in enumerate(points)
    ]
    glow_img = img.copy()
    glow_draw = ImageDraw.Draw(glow_img)
    glow_draw.polygon(glow_points, fill="#ffaa00", outline="#ffaa00")
    img = Image.blend(glow_img, img, 0.7)
    draw = ImageDraw.Draw(img)
    draw.polygon(points, fill=bolt_color, outline=bolt_color)

    return img


def main():
    """Generate both icon sizes."""
    sizes = [192, 512]

    for size in sizes:
        print(f"Generating icon-{size}.png...")
        icon = create_icon(size)
        icon_path = f"static/icon-{size}.png"
        icon.save(icon_path, format="PNG", optimize=True)
        print(f"✓ Created {icon_path}")

    print("\nIcons generated successfully!")


if __name__ == "__main__":
    try:
        main()
    except ImportError:
        print("Error: Pillow (PIL) is required to generate icons.")
        print("Install it with: uv pip install Pillow")
        exit(1)
