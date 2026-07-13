package app;

import org.junit.jupiter.api.Test;

public class WidgetIndirectTest {

	@Test
	public void rendersIndirectly() {
		WidgetHelper helper = new WidgetHelper();
		helper.callWidget();
	}
}
