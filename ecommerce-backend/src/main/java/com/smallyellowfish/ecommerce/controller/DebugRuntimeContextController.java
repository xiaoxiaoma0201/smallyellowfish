package com.smallyellowfish.ecommerce.controller;

import com.smallyellowfish.ecommerce.dto.ApiResponse;
import com.smallyellowfish.ecommerce.dto.RuntimeOrderContextResponse;
import com.smallyellowfish.ecommerce.service.RuntimeOrderContextService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "Debug", description = "仅供调试后台构造演示 Runtime Context")
@Profile("debug")
@RestController
@RequestMapping("/api/debug/users")
public class DebugRuntimeContextController {

    private final RuntimeOrderContextService runtimeOrderContextService;

    public DebugRuntimeContextController(RuntimeOrderContextService runtimeOrderContextService) {
        this.runtimeOrderContextService = runtimeOrderContextService;
    }

    @GetMapping("/{userId}/order-context")
    @Operation(
        summary = "List debug-safe order context",
        description = "仅在 debug Profile 下提供演示用户订单安全摘要；生产客服网关仍以登录态身份构造上下文"
    )
    public ApiResponse<RuntimeOrderContextResponse> listOrderContext(
        @PathVariable String userId,
        @RequestParam(value = "month", required = false) Integer month,
        @RequestParam(value = "limit", required = false) Integer limit) {
        return ApiResponse.success(runtimeOrderContextService.loadCurrentUserOrders(userId, month, limit));
    }
}
