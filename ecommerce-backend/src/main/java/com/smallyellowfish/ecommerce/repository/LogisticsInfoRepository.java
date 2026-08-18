package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.LogisticsInfo;
import com.smallyellowfish.ecommerce.entity.OrderEntity;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LogisticsInfoRepository extends JpaRepository<LogisticsInfo, Long> {

    Optional<LogisticsInfo> findByOrderEntity(OrderEntity orderEntity);
}
