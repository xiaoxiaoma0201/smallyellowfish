package com.smallyellowfish.ecommerce.controller;

import com.smallyellowfish.ecommerce.dto.AdminUserBalanceResponse;
import com.smallyellowfish.ecommerce.dto.ApiResponse;
import com.smallyellowfish.ecommerce.service.AdminUserService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/admin/users")
public class AdminUserController {

    private final AdminUserService adminUserService;

    public AdminUserController(AdminUserService adminUserService) {
        this.adminUserService = adminUserService;
    }

    @GetMapping("/{userId}")
    public ApiResponse<AdminUserBalanceResponse> getUser(@PathVariable String userId) {
        return ApiResponse.success(adminUserService.getUserBalance(userId));
    }
}
