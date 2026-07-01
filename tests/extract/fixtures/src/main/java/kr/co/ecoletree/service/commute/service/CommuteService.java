package kr.co.ecoletree.service.commute.service;

import javax.servlet.http.HttpServletRequest;
import java.text.ParseException;
import java.util.Map;

public interface CommuteService {
	
	/**
	 * @param param
	 * @return
	 */
	public Map<String, Object> insertGotoWork(Map<String, Object> param, HttpServletRequest request);
	
	/**
	 * @param param
	 * @return
	 */
	public Map<String, Object> updateOffWork(Map<String, Object> param);

	/**
	 * @param param
	 * @return
	 */
	public Map<String, Object> selectAttedanceCondition(Map<String, Object> param);

	/**
	 * @param paramData
	 * @return
	 */
	public Map<String, Object> insertGetVacation(Map<String, Object> paramData);

	/**
	 * @param map
	 * @return
	 */
	public Map<String, Object> selectCodeList(Map<String, Object> map);

	/**
	 * @param param
	 * @return
	 * @throws ParseException
	 */
	public Map<String, Object> selectWorkTimeMonthHandler(Map<String, Object> param) throws ParseException;



	/**
	 * 사용휴가, 남은휴가 카운트 조회
	 * @param param
	 * @return
	 */
	public Map<String, Object> getDayOffCondition(Map<String, Object> param);

	/**
	 * 출퇴근부 수정 권한 확인
	 * @param param
	 * @return
	 */
	public boolean checkEditable(Map<String, Object> param);

	/**
     * 출근 취소하기
     *
     * @param param
     * @return
     */
	public boolean cancelAttendance(Map<String, Object> param);

	/**
	 * 유급휴가 취소하기
	 * @param param
	 * @return
	 */
	public boolean cancelVacation(Map<String, Object> param);

	
}
