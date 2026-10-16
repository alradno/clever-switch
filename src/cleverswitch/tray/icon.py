from PIL import Image, ImageDraw


def create_icon(paused=False, mac=False):
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    color = (125, 125, 125, 255) if paused else ((30, 30, 30, 255) if mac else (45, 170, 145, 255))
    draw.rounded_rectangle((3, 3, 61, 61), radius=15, fill=color)
    draw.line((14, 23, 47, 23), fill="white", width=5)
    draw.polygon(((43, 14), (53, 23), (43, 32)), fill="white")
    draw.line((50, 42, 17, 42), fill="white", width=5)
    draw.polygon(((21, 33), (11, 42), (21, 51)), fill="white")
    return image
