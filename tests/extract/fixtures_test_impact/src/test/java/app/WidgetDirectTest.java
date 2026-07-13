package app;

import org.junit.jupiter.api.Test;

public class WidgetDirectTest {

	@Test
	public void rendersDirectly() {
		Widget widget = new Widget();
		widget.render();
	}
}
