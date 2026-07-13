package kr.co.ecoletree.order

import org.springframework.stereotype.Service

@Service
class OrderService(private val repo: OrderRepo) {

    fun find(id: String): String {
        return repo.load(id)
    }
}
