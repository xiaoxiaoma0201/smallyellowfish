package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.RefundRequest;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RefundRequestRepository extends JpaRepository<RefundRequest, Long> {

    Optional<RefundRequest> findByRequestId(String requestId);
}
