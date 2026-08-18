package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.OrderEntity;
import com.smallyellowfish.ecommerce.entity.OrderItem;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderItemRepository extends JpaRepository<OrderItem, Long> {

    List<OrderItem> findByOrderEntity(OrderEntity orderEntity);
}
