package app;

import org.junit.jupiter.api.Test;

public class WidgetViaProdTest {

	@Test
	public void rendersViaProd() {
		WidgetProdHelper helper = new WidgetProdHelper();
		helper.callWidget();
	}
}
