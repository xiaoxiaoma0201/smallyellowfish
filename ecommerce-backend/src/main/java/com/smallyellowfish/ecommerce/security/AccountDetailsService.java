package com.smallyellowfish.ecommerce.security;

import com.smallyellowfish.ecommerce.repository.AppAccountRepository;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;

@Service
public class AccountDetailsService implements UserDetailsService {

    private final AppAccountRepository appAccountRepository;

    public AccountDetailsService(AppAccountRepository appAccountRepository) {
        this.appAccountRepository = appAccountRepository;
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        return appAccountRepository.findByUsername(username)
            .map(AccountPrincipal::new)
            .orElseThrow(() -> new UsernameNotFoundException("Account not found: " + username));
    }
}
