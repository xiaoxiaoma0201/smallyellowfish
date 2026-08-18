package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.UserCoupon;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserCouponRepository extends JpaRepository<UserCoupon, Long> {

    List<UserCoupon> findByUserIdAndStatus(String userId, String status);
}
