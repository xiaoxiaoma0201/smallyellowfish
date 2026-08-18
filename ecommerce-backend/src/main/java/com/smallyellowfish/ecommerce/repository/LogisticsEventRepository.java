package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.LogisticsEvent;
import com.smallyellowfish.ecommerce.entity.LogisticsInfo;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LogisticsEventRepository extends JpaRepository<LogisticsEvent, Long> {

    List<LogisticsEvent> findByLogisticsInfoOrderByOccurredAtDesc(LogisticsInfo logisticsInfo);
}
