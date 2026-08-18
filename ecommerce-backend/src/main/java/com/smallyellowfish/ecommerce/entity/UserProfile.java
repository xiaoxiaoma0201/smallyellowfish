package com.smallyellowfish.ecommerce.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;

@Entity
public class UserProfile {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String userId;

    private String nickname;

    private String mobile;

    private String memberLevel;

    private String riskLevel;

    /** 客服角色门控用：buyer / seller（李四 U1002 为卖家，其余演示账号为买家）。 */
    private String side;

    protected UserProfile() {
    }

    public UserProfile(String userId, String nickname, String memberLevel, String riskLevel) {
        this(userId, nickname, null, memberLevel, riskLevel, null);
    }

    public UserProfile(String userId, String nickname, String mobile, String memberLevel, String riskLevel) {
        this(userId, nickname, mobile, memberLevel, riskLevel, null);
    }

    public UserProfile(String userId, String nickname, String mobile, String memberLevel, String riskLevel, String side) {
        this.userId = userId;
        this.nickname = nickname;
        this.mobile = mobile;
        this.memberLevel = memberLevel;
        this.riskLevel = riskLevel;
        this.side = side;
    }

    public Long getId() {
        return id;
    }

    public String getUserId() {
        return userId;
    }

    public String getNickname() {
        return nickname;
    }

    public String getMobile() {
        return mobile;
    }

    public String getMemberLevel() {
        return memberLevel;
    }

    public String getRiskLevel() {
        return riskLevel;
    }

    public void updateDisplayProfile(String nickname, String mobile) {
        this.nickname = nickname;
        this.mobile = mobile;
    }

    public String getSide() {
        return side;
    }

    public void setSide(String side) {
        this.side = side;
    }
}
