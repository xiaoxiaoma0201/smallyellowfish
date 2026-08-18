package com.smallyellowfish.ecommerce.repository;

import com.smallyellowfish.ecommerce.entity.AppAccount;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AppAccountRepository extends JpaRepository<AppAccount, Long> {

    Optional<AppAccount> findByUsername(String username);
}
