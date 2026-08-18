package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.ApprovalRequest;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ApprovalRequestRepository extends JpaRepository<ApprovalRequest, Long> {

    Optional<ApprovalRequest> findByApprovalId(String approvalId);

    List<ApprovalRequest> findByBusinessTypeAndBusinessIdOrderByCreatedAtDesc(String businessType, String businessId);
}
