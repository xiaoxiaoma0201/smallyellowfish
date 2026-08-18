package com.smallyellowfish.ecommerce.controller;

import com.smallyellowfish.ecommerce.dto.ApiResponse;
import com.smallyellowfish.ecommerce.dto.CurrentAccountResponse;
import com.smallyellowfish.ecommerce.security.AccountPrincipal;
import com.smallyellowfish.ecommerce.service.AuthService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "Admin", description = "管理员后台登录态验证接口")
@RestController
@RequestMapping("/api/admin")
public class AdminMeController {

    private final AuthService authService;

    public AdminMeController(AuthService authService) {
        this.authService = authService;
    }

    @GetMapping("/me")
    @Operation(summary = "Get current admin identity")
    public ApiResponse<CurrentAccountResponse> me(@AuthenticationPrincipal AccountPrincipal principal) {
        return ApiResponse.success(authService.toResponse(principal));
    }
}
