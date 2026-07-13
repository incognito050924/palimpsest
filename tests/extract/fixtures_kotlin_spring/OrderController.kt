package kr.co.ecoletree.order

import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable

@RestController
@RequestMapping("/api/v1/orders")
class OrderController(private val service: OrderService) {

    @GetMapping("/{id}")
    fun get(@PathVariable id: String): String {
        return service.find(id)
    }
}
