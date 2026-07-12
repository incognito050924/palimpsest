package kr.co.ecoletree.service.report.service;

import java.util.Map;

/**
 * Unrelated service that happens to declare a method whose simple name
 * ({@code selectCodeList}) collides with {@code CommuteService#selectCodeList}.
 * The controller never references this type, so a receiver-typed CALLS resolver
 * must NOT link the controller's {@code service.selectCodeList(param)} here.
 */
public interface ReportService {

	Map<String, Object> selectCodeList(Map<String, Object> param);
}
