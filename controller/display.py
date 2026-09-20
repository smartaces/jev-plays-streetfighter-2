"""Draw through pyglet's event loop, with Retina-aware sizing."""


def fitted_rectangle(width, height, aspect=4 / 3):
    """Fit the game's TV aspect ratio into logical window coordinates."""
    draw_width = min(width, height * aspect)
    draw_height = draw_width / aspect
    return ((width - draw_width) / 2, (height - draw_height) / 2,
            draw_width, draw_height)


class GameDisplay:
    def __init__(self, frame):
        import pyglet
        from pyglet import gl
        self.pyglet, self.gl = pyglet, gl
        screen = pyglet.canvas.get_display().get_default_screen()
        scale = min(1, screen.width * 0.9 / 960, (screen.height - 80) / 720)
        self.window = pyglet.window.Window(
            width=int(960 * scale), height=int(720 * scale),
            caption="Jev SF2 — Space: start/pause | R: reset | Esc: quit",
            resizable=True, vsync=True, visible=False,
        )
        self.window.set_minimum_size(480, 360)
        self.texture = None
        self.pending = None
        self.last_frame = None
        self.window.push_handlers(on_draw=self.draw)
        self.set_frame(frame)
        self.window.set_visible(True)

    def set_frame(self, frame):
        # SFII uses a 256-pixel raster; the core pads it to 320 on the right.
        # Keep the complete height, including both health bars and the timer.
        if frame is self.last_frame:
            return
        self.last_frame = frame
        visible = frame[:, :256, :]
        height, width, _ = visible.shape
        self.pending = self.pyglet.image.ImageData(
            width, height, "RGB", visible.tobytes(), pitch=-width * 3)
        self.window.invalid = True

    def draw(self):
        gl = self.gl
        width, height = self.window.get_size()
        framebuffer_width, framebuffer_height = self.window.get_framebuffer_size()
        if min(width, height, framebuffer_width, framebuffer_height) <= 0:
            return self.pyglet.event.EVENT_HANDLED

        # Cocoa window sizes are points; the viewport requires backing pixels.
        gl.glViewport(0, 0, framebuffer_width, framebuffer_height)
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        gl.glOrtho(0, width, 0, height, -1, 1)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()
        gl.glClearColor(0, 0, 0, 1)
        self.window.clear()

        if self.pending is not None:
            image = self.pending
            if self.texture is None or (self.texture.width, self.texture.height) != (image.width, image.height):
                self.texture = self.pyglet.image.Texture.create(image.width, image.height)
                gl.glBindTexture(self.texture.target, self.texture.id)
                gl.glTexParameteri(self.texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
                gl.glTexParameteri(self.texture.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
            self.texture.blit_into(image, 0, 0, 0)
            self.pending = None
        if self.texture is not None:
            x, y, draw_width, draw_height = fitted_rectangle(width, height)
            self.texture.blit(x, y, width=draw_width, height=draw_height)

        # The native event loop flips once after on_draw. Never flip here or
        # dispatch events from the frame-update callback: both caused flicker.
        return self.pyglet.event.EVENT_HANDLED

    def close(self):
        # Pyglet owns texture cleanup, including non-power-of-two TextureRegions.
        # Release references while the window's graphics context still exists.
        self.texture = None
        self.pending = None
        self.last_frame = None
        self.window.close()
