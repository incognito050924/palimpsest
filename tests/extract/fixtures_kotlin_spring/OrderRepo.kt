package kr.co.ecoletree.order

import org.springframework.stereotype.Repository

@Repository
class OrderRepo {

    fun load(id: String): String {
        return id
    }
}
