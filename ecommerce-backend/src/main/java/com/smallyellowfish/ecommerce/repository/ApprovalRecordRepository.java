package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.ApprovalRecord;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ApprovalRecordRepository extends JpaRepository<ApprovalRecord, Long> {

    List<ApprovalRecord> findByTargetNoOrderByCreatedAtDesc(String targetNo);
}
